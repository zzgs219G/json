#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Trending 采集模块
========================

职责：抓取 GitHub Trending 页面（语言 × 时间维度），解析为结构化 JSON，
供 jian_box App 端展示"今日/本周/本月"热门项目并支持按语言筛选。

产出物（backend/github_trending/，按时间维度合并，避免碎片化小文件）：
  daily.json              # 今日全部语言
  weekly.json             # 本周全部语言
  monthly.json            # 本月全部语言
  每个文件内 reposByLanguage 按"语言显示名 -> 仓库数组"组织，自描述，无需单独 index.json

设计原则（与 antutu_crawler.py 风格一致）：
  - 仅解析公开 trending HTML，不调用任何需要 GitHub API 的接口（见文档约束）
  - 并发抓取（ThreadPoolExecutor, 4 线程）：单请求实测 2.3~4.0s，
    串行 + 1.5s 间隔 45 个请求约 216s，无法满足文档"<120s"验收标准；
    温和并发在保证不触发限流的前提下约 50s 完成
  - 单个语言/维度失败不影响整体（降级处理），输出时对应语言不登记进维度文件
  - 所有网络请求带超时与重试

数据源：
  https://github.com/trending/{language}?since={since}
  language 为空表示"全部"；since ∈ {daily, weekly, monthly}

用法：
  python3 scripts/github_trending.py                          # 输出到 backend/github_trending
  python3 scripts/github_trending.py --out /tmp/trending      # 指定输出目录

依赖：requests + beautifulsoup4（工作流中 pip install 安装）。
"""

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

# ============================================================================
# 配置
# ============================================================================

TRENDING_URL = "https://github.com/trending/{lang}?since={since}"

# 抓取语言（含"全部"），文档第 4 节列表；slug 为 URL 路径片段（c++/c# 需 URL 编码）
LANGUAGES = [
    # (显示名, slug, 文件名小写标识)
    ("全部", "", "all"),
    ("Python", "python", "python"),
    ("JavaScript", "javascript", "javascript"),
    ("TypeScript", "typescript", "typescript"),
    ("Go", "go", "go"),
    ("Rust", "rust", "rust"),
    ("Java", "java", "java"),
    ("C++", "c++", "cpp"),
    ("C#", "c#", "csharp"),
    ("PHP", "php", "php"),
    ("Swift", "swift", "swift"),
    ("Kotlin", "kotlin", "kotlin"),
    ("Dart", "dart", "dart"),
    ("Ruby", "ruby", "ruby"),
    ("Shell", "shell", "shell"),
]

DIMENSIONS = [
    # (since 值, 显示标签)
    ("daily", "今日"),
    ("weekly", "本周"),
    ("monthly", "本月"),
]

# 浏览器 UA（文档第 8 节要求主流浏览器标识）
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

REQUEST_INTERVAL = 1.5   # 文档要求的请求间隔下限（重试间退避使用）
REQUEST_TIMEOUT = 20     # 文档要求 20 秒超时
MAX_RETRIES = 2          # 单页重试次数
MAX_WORKERS = 4          # 温和并发：4 线程下每线程有效间隔 ≈ 6s，远高于 1.5s 要求
MIN_REPOS = 10           # 文档验收：单维度至少 10 条

CST = timezone(timedelta(hours=8))

# 数字文本中的千分位逗号，如 "39,584"
NUM_RE = re.compile(r"[\d,]+")

# ============================================================================
# 网络请求
# ============================================================================

def fetch_html(url):
    """获取页面 HTML，带重试与超时；失败抛 RuntimeError。"""
    last_err = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            html = resp.text
            if not html or "<html" not in html.lower():
                raise RuntimeError("响应体异常(疑似被拦截)")
            return html
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"[warn] 第 {attempt + 1} 次请求失败 {url}: {e}", file=sys.stderr)
            if attempt < MAX_RETRIES:
                time.sleep(REQUEST_INTERVAL)
    raise RuntimeError(f"页面请求最终失败: {url} -> {last_err}")


# ============================================================================
# 解析
# ============================================================================

def _parse_count(text):
    """解析 '39,584' / '1,114 stars today' 中的数字；失败返回 None。"""
    m = NUM_RE.search(text or "")
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_trending(html):
    """解析 trending 页面，返回仓库对象数组（文档第 3 节字段）。"""
    soup = BeautifulSoup(html, "html.parser")
    repos = []
    for block in soup.select("article.Box-row"):
        # nameWithOwner / repoUrl：h2 a[href]，形如 /owner/repo
        link = block.select_one("h2 a[href]")
        if link is None:
            continue
        path = link.get("href", "").strip("/")
        if not path or "/" not in path:
            continue
        owner, _, repo = path.partition("/")

        desc_node = block.select_one("p")
        lang_node = block.select_one("[itemprop=programmingLanguage]")
        # 两个 a.Link--muted 依次为总 star 数和 fork 数
        muted = block.select("a.Link--muted")
        stars = _parse_count(muted[0].get_text()) if len(muted) > 0 else None
        forks = _parse_count(muted[1].get_text()) if len(muted) > 1 else None
        today_node = block.select_one("span.d-inline-block.float-sm-right")

        repos.append({
            "nameWithOwner": f"{owner}/{repo}",
            "repoUrl": f"https://github.com/{owner}/{repo}",
            "description": desc_node.get_text(strip=True) if desc_node else "",
            "language": lang_node.get_text(strip=True) if lang_node else "",
            "stargazerCount": stars or 0,
            "forkCount": forks or 0,
            "todayStars": _parse_count(today_node.get_text()) if today_node else 0,
        })
    return repos


# ============================================================================
# 组装与落盘
# ============================================================================

def fetch_one(slug, since):
    """抓取单个 语言×维度，返回 (slug, since, repos)。失败返回 repos=[]。"""
    url = TRENDING_URL.format(lang=slug, since=since)
    try:
        html = fetch_html(url)
        repos = parse_trending(html)
        if not repos:
            print(f"[warn] 解析结果为空（页面结构变化或无数据）: {url}", file=sys.stderr)
        return slug, since, repos
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 抓取失败(降级跳过): {url} -> {e}", file=sys.stderr)
        return slug, since, []


def build_payload(out_dir):
    """并发抓取全部组合，按时间维度合并落盘（每维度一个 JSON 文件）。"""
    os.makedirs(out_dir, exist_ok=True)

    results = {}   # (slug, since) -> repos
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_one, slug, since): (slug, since)
                   for (_, slug, _) in LANGUAGES for (since, _) in DIMENSIONS}
        for fut in as_completed(futures):
            slug, since, repos = fut.result()
            results[(slug, since)] = repos

    generated_at = datetime.now(CST).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    ok_combos = 0
    empty_logged = []

    for (since, label) in DIMENSIONS:
        repos_by_language = {}
        for (name, slug, ident) in LANGUAGES:
            repos = results.get((slug, since), [])
            if not repos:
                empty_logged.append(f"{ident}_{since}")
                continue  # 降级：该语言无数据则不在该维度文件中登记
            repos_by_language[name] = repos
            ok_combos += 1
        payload = {
            "dimension": {"since": since, "label": label},
            "updatedAt": generated_at,
            "languages": list(repos_by_language.keys()),
            "reposByLanguage": repos_by_language,
        }
        rel_path = f"{since}.json"
        with open(os.path.join(out_dir, rel_path), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"   产出 {rel_path}（{len(repos_by_language)} 种语言, "
              f"{sum(len(v) for v in repos_by_language.values())} 个仓库）")

    print(f"✅ 产出 {out_dir}/{{{','.join(s for s, _ in DIMENSIONS)}}}.json，"
          f"共 {ok_combos} 个 语言×维度 组合")
    if empty_logged:
        print(f"   [降级] 以下组合无数据/抓取失败，未登记: {', '.join(empty_logged)}")


def main():
    parser = argparse.ArgumentParser(description="GitHub Trending 采集脚本")
    parser.add_argument("--out", default="backend/github_trending",
                        help="输出目录（默认 backend/github_trending）")
    args = parser.parse_args()

    t0 = time.time()
    build_payload(args.out)
    print(f"   总耗时 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
