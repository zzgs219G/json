#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
安兔兔排行榜云端爬虫
===================

职责：在云端定时任务(GitHub Actions)中抓取安兔兔各榜单页面，解析后合并为单一 JSON 文件，
供 App 端 AntutuFetcher 拉取展示（代替 App 实时爬取 HTML）。

产出物：backend/antutu/antutu_rank.json
JSON 结构与 App 端数据模型(AntutuModel.kt)字段一一对齐，App 端反序列化开启 ignoreUnknownKeys，
新增字段不会破坏旧逻辑。

榜单：
- 性能榜  rank101.htm        -> rank.items
- 性价比榜 rank200~rank205   -> cost.tiers[]（6 个价格档位）
- 好评榜  judge.htm          -> judge.items
- SoC 天梯 rank301.htm       -> soc.items
- 各榜单顶部「榜单详解」#ranking_news -> 各 section 的 brief

实现参照 App 端 AntutuParser.kt 的 Jsoup 解析逻辑（保持两端解析策略一致）。

用法：
    python3 scripts/antutu_crawler.py                     # 线上抓取，输出 backend/antutu/antutu_rank.json
    python3 scripts/antutu_crawler.py --out /tmp/x.json   # 指定输出路径
    # 离线验证（不访问网络，从本地 HTML 读取，仅用于测试解析逻辑）：
    python3 scripts/antutu_crawler.py --rank-html docs/安兔兔.html --out /tmp/x.json

依赖：requests + beautifulsoup4（工作流中 pip install 安装）。
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

# ============================================================================
# 榜单 URL 配置（与 AntutuParser.kt 保持一致）
# ============================================================================

RANK_URL = "https://www.antutu.com/ranking/rank101.htm"
RANK_TITLE = "手机性能排行榜"

COST_TIERS = [
    ("全部价位", "https://www.antutu.com/ranking/rank200.htm"),
    ("¥1499以下", "https://www.antutu.com/ranking/rank201.htm"),
    ("¥1500-2499", "https://www.antutu.com/ranking/rank202.htm"),
    ("¥2500-3499", "https://www.antutu.com/ranking/rank203.htm"),
    ("¥3500-4499", "https://www.antutu.com/ranking/rank204.htm"),
    ("¥4500以上", "https://www.antutu.com/ranking/rank205.htm"),
]
COST_TITLE = "手机性价比排行榜"

JUDGE_URL = "https://www.antutu.com/ranking/judge.htm"
JUDGE_TITLE = "手机好评率排行榜"

SOC_URL = "https://www.antutu.com/ranking/rank301.htm"
SOC_TITLE = "SoC 天梯榜"

# 浏览器 UA（与 AntutuParser.kt 一致），避免被识别为爬虫
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
)

# 请求间隔（秒），降低触发反爬概率
REQUEST_INTERVAL = 1.5
# 单页请求重试次数
MAX_RETRIES = 2

# 性价比条目内存文本中的价格，如 "(S-8 Elite 12+256) ¥4699" 中的 4699
PRICE_RE = re.compile(r"¥(\d+)")

CST = timezone(timedelta(hours=8))


# ============================================================================
# 网络请求
# ============================================================================

def fetch_html(url, from_file=None):
    """获取页面 HTML。from_file 提供时从本地文件读取（离线测试用），否则发起网络请求。"""
    if from_file is not None:
        if not os.path.isfile(from_file):
            raise FileNotFoundError(f"本地 HTML 文件不存在: {from_file}")
        with open(from_file, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    last_err = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Referer": "https://www.antutu.com/ranking",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
                timeout=20,
            )
            resp.raise_for_status()
            # 优先按响应头编码解码，退化到探测
            if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = resp.apparent_encoding or "utf-8"
            html = resp.text
            if not html or "<html" not in html.lower():
                raise RuntimeError(f"响应体异常(疑似被拦截): {url}")
            return html
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"[warn] 第 {attempt + 1} 次请求失败 {url}: {e}", file=sys.stderr)
            if attempt < MAX_RETRIES:
                time.sleep(REQUEST_INTERVAL)
    raise RuntimeError(f"页面请求最终失败: {url} -> {last_err}")


# ============================================================================
# 解析函数（与 AntutuParser.kt 的 Jsoup 逻辑一一对应）
# ============================================================================

def parse_brief(html):
    """榜单详解文本：#ranking_news。"""
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one("#ranking_news")
    return node.get_text(strip=True) if node else ""


def parse_rank(html):
    """性能榜：每个条目一个 ul.newrank-b。"""
    soup = BeautifulSoup(html, "html.parser")
    result = []
    for block in soup.select("ul.newrank-b"):
        rank_node = block.select_one(".numrank")
        name_node = block.select_one(".model-name")
        mem_node = block.select_one(".memory")
        if rank_node is None or name_node is None:
            continue
        rank = _to_int(rank_node.get_text(strip=True))
        name = name_node.get_text(strip=True)
        if rank is None or not name:
            continue
        memory = mem_node.get_text(strip=True) if mem_node else ""
        # 块内所有纯数字 li 文本，依次对应 CPU/GPU/MEM/UX（排名 li 和总分 li 含非数字字符会被过滤）
        scores = [_to_long(li.get_text(strip=True)) for li in block.select("li")]
        scores = [s for s in scores if s is not None]
        if len(scores) < 4:
            continue
        blast_node = block.select_one(".blast")
        total = _to_long("".join(re.findall(r"\d", blast_node.get_text(strip=True)))) if blast_node else 0
        result.append({
            "rank": rank,
            "modelName": name,
            "memory": memory,
            "cpu": scores[0],
            "gpu": scores[1],
            "mem": scores[2],
            "ux": scores[3],
            "total": total,
        })
    return result


def parse_cost(html):
    """性价比榜：每个条目一个 ul.newrankxjb。"""
    soup = BeautifulSoup(html, "html.parser")
    result = []
    for block in soup.select("ul.newrankxjb"):
        rank_node = block.select_one(".numrank")
        name_node = block.select_one(".model-name")
        mem_node = block.select_one(".memory")
        score_node = block.select_one(".newrankd")
        if rank_node is None or name_node is None:
            continue
        rank = _to_int(rank_node.get_text(strip=True))
        name = name_node.get_text(strip=True)
        if rank is None or not name:
            continue
        memory_full = mem_node.get_text(strip=True) if mem_node else ""
        m = PRICE_RE.search(memory_full)
        price = int(m.group(1)) if m else 0
        memory = PRICE_RE.sub("", memory_full).strip()
        score = _to_double(score_node.get_text(strip=True)) if score_node else 0.0
        result.append({
            "rank": rank,
            "modelName": name,
            "memory": memory,
            "price": price,
            "score": score,
        })
    return result


def parse_judge(html):
    """好评率榜：每个条目一个 ul.newrankxjb，表头为「手机名称 | 好评率」。"""
    soup = BeautifulSoup(html, "html.parser")
    result = []
    for block in soup.select("ul.newrankxjb"):
        rank_node = block.select_one(".numrank")
        name_node = block.select_one(".model-name")
        if rank_node is None or name_node is None:
            continue
        rank = _to_int(rank_node.get_text(strip=True))
        name = name_node.get_text(strip=True)
        if rank is None or not name:
            continue
        rate_texts = [li.get_text(strip=True) for li in block.select("li")]
        rate_text = next((t for t in rate_texts if t.endswith("%")), None)
        if rate_text is None:
            continue
        rate = _to_double(rate_text.rstrip("%"))
        if rate is None:
            continue
        result.append({"rank": rank, "modelName": name, "goodRate": rate})
    return result


def parse_soc(html):
    """SoC 天梯榜：每个条目一个 ul.newrank-c。"""
    soup = BeautifulSoup(html, "html.parser")
    result = []
    for block in soup.select("ul.newrank-c"):
        rank_node = block.select_one(".numrank")
        name_node = block.select_one(".model-name")
        mem_node = block.select_one(".memory")
        if rank_node is None or name_node is None:
            continue
        rank = _to_int(rank_node.get_text(strip=True))
        name = name_node.get_text(strip=True)
        if rank is None or not name:
            continue
        spec = mem_node.get_text(strip=True) if mem_node else ""
        scores = [_to_long(li.get_text(strip=True)) for li in block.select("li")]
        scores = [s for s in scores if s is not None]
        if len(scores) < 3:
            continue
        result.append({
            "rank": rank,
            "socName": name,
            "spec": spec,
            "cpu": scores[0],
            "gpu": scores[1],
            "total": scores[2],
        })
    return result


def _to_int(text):
    try:
        return int(text.replace(",", ""))
    except (ValueError, TypeError):
        return None


def _to_long(text):
    try:
        return int(text.replace(",", ""))
    except (ValueError, TypeError):
        return None


def _to_double(text):
    try:
        return float(text.replace(",", "").replace("分", ""))
    except (ValueError, TypeError):
        return None


# ============================================================================
# 组装与落盘
# ============================================================================

def build_payload(args):
    """抓取全部榜单并组装为最终 JSON 结构（dict）。"""
    rank_html = fetch_html(RANK_URL, from_file=args.rank_html)
    time.sleep(REQUEST_INTERVAL)

    rank_items = parse_rank(rank_html)
    rank_brief = parse_brief(rank_html)

    cost_brief = ""
    cost_tiers = []
    for i, (label, url) in enumerate(COST_TIERS):
        html = fetch_html(url, from_file=args.cost_htmls[i] if args.cost_htmls and i < len(args.cost_htmls) else None)
        if i > 0:
            time.sleep(REQUEST_INTERVAL)
        items = parse_cost(html)
        if i == 0:
            cost_brief = parse_brief(html)
        cost_tiers.append({"label": label, "items": items})

    judge_html = fetch_html(JUDGE_URL, from_file=args.judge_html)
    time.sleep(REQUEST_INTERVAL)
    judge_items = parse_judge(judge_html)
    judge_brief = parse_brief(judge_html)

    soc_html = fetch_html(SOC_URL, from_file=args.soc_html)
    soc_items = parse_soc(soc_html)
    soc_brief = parse_brief(soc_html)

    return {
        "generatedAt": datetime.now(CST).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "rank": {"title": RANK_TITLE, "brief": rank_brief, "items": rank_items},
        "cost": {
            "title": COST_TITLE,
            "brief": cost_brief,
            "tiers": cost_tiers,
        },
        "judge": {"title": JUDGE_TITLE, "brief": judge_brief, "items": judge_items},
        "soc": {"title": SOC_TITLE, "brief": soc_brief, "items": soc_items},
    }


def main():
    parser = argparse.ArgumentParser(description="安兔兔排行榜云端爬虫")
    parser.add_argument("--out", default="backend/antutu/antutu_rank.json",
                        help="输出 JSON 路径（默认 backend/antutu/antutu_rank.json）")
    # 离线测试参数：提供后从本地文件读取对应榜单 HTML，而非网络请求
    parser.add_argument("--rank-html", default=None, help="性能榜本地 HTML 文件（离线测试）")
    parser.add_argument("--cost-htmls", nargs="*", default=None, help="性价比 6 档本地 HTML 文件列表（离线测试）")
    parser.add_argument("--judge-html", default=None, help="好评榜本地 HTML 文件（离线测试）")
    parser.add_argument("--soc-html", default=None, help="SoC 榜本地 HTML 文件（离线测试）")
    args = parser.parse_args()

    payload = build_payload(args)

    out_path = args.out
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # 汇总统计
    def stat(items):
        return len(items)

    print(f"✅ 产出 {out_path}")
    print(f"   rank.items = {stat(payload['rank']['items'])}")
    print(f"   cost.tiers = {[stat(t['items']) for t in payload['cost']['tiers']]}")
    print(f"   judge.items = {stat(payload['judge']['items'])}")
    print(f"   soc.items = {stat(payload['soc']['items'])}")


if __name__ == "__main__":
    main()
