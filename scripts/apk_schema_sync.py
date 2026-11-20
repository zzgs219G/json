#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
加固配置 schema 镜像抓取
========================

职责：把 artifact-pipeline 仓库里的 configs/v{N}/config-schema.json 拉到本
仓库的 backend/apk_schema/v{N}/ 下，供 App 端 ProtectSchemaLoader 读取。

为什么要镜像这一份：App 原先直连
    raw.giteeusercontent.com/zzgs219/apk-protect-action/raw/main/configs/...
    （旧仓库名。2026-11 迁移后源仓库是 zzgs219G/artifact-pipeline；
      旧 apk-protect-action 虽仍存在但已转为【私有】，匿名拉取会 404）
把 owner/repo 明文写在了请求 URL 里，抓包或反编译即可看到。镜像到本仓库后
App 只需知道 zzgs219/json（这个仓库本来就公开，jian_box.enc 也在这里），
不额外暴露任何东西。

为什么不做定时：这份 schema 是本仓库主人的自有文件，不改就不会变。与
antutu / github_star 那类"抓别人会变的数据"不同，只在源仓库 push 时由
repository_dispatch 触发即可（见 artifact-pipeline 的
.github/workflows/notify-json-mirror.yml）。

用法：
    python3 scripts/apk_schema_sync.py                  # 拉所有已知版本
    python3 scripts/apk_schema_sync.py --version 1      # 只拉 v1
    python3 scripts/apk_schema_sync.py --dry-run        # 只报告不写文件

依赖：仅标准库（urllib），无需 pip install。

退出码：
    0  成功（可能有/没有变更）
    1  拉取失败（源文件不存在 / 网络错误 / 内容不是 JSON）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

# 源仓库坐标。owner/repo 只出现在本脚本里，不进 APK。
SOURCE_OWNER = "zzgs219G"
SOURCE_REPO = "artifact-pipeline"
SOURCE_REF = "main"

OUT_DIR_TEMPLATE = "backend/apk_schema/v{version}"
OUT_FILE = "config-schema.json"

# 需要同步的 schema 大版本。
#
# ⚠️ 2026-11-21 改为【自动发现】：原来是硬编码 SUPPORTED_VERSIONS = [1]，
# 后果是 artifact-pipeline 新增 configs/v2 后本仓库完全没镜像它 ——
# src 那边发了 repository_dispatch、本仓库 workflow 也绿了、日志只循环了 v1，
# 全程没有任何报错。这个 bug 的形态是「成功但漏做」，最难发现的一类。
#
# 现在通过 GitHub Contents API 列 configs/ 目录，发现所有 v<N> 子目录。
# 新增卡片只需在源仓库建目录，本仓库零改动。
#
# 兜底：列目录失败（API 限流/网络）时退回这份保守清单，并打警告 ——
# 宁可少拉也不要让整个同步变成红，但必须让人知道降级了。
FALLBACK_VERSIONS = [1, 2]

# 保留可被 --version 覆盖；None 表示「自动发现」
SUPPORTED_VERSIONS = None

# GitHub Contents API 走 raw 媒体类型可直接拿文件原文
RAW_ACCEPT = "application/vnd.github.raw"


def source_url(version: int) -> str:
    return (
        f"https://api.github.com/repos/{SOURCE_OWNER}/{SOURCE_REPO}"
        f"/contents/configs/v{version}/{OUT_FILE}?ref={SOURCE_REF}"
    )


def discover_versions(token: str | None) -> list[int]:
    """列出源仓库 configs/ 下所有 v<N> 目录，返回版本号升序列表。

    为什么用 Contents API 而不是硬编码清单：硬编码的形态是「新增卡片必须
    回来改本文件」，而漏改时**不会报错**——2026-11-21 实际踩过：源仓库加了
    configs/v2，本仓库照常绿、照常提交，只是只拉了 v1。这类「成功但漏做」
    的 bug 靠人肉纪律防不住。
    """
    url = (
        f"https://api.github.com/repos/{SOURCE_OWNER}/{SOURCE_REPO}"
        f"/contents/configs?ref={SOURCE_REF}"
    )
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", "json-repo-schema-sync")

    with urllib.request.urlopen(req, timeout=30) as resp:
        entries = json.loads(resp.read().decode("utf-8"))

    if not isinstance(entries, list):
        raise RuntimeError(f"configs/ 列目录返回结构异常: {type(entries).__name__}")

    versions = []
    for e in entries:
        if not isinstance(e, dict) or e.get("type") != "dir":
            continue
        name = e.get("name") or ""
        m = re.fullmatch(r"v(\d+)", name)
        if m:
            versions.append(int(m.group(1)))
    return sorted(set(versions))


def fetch(version: int, token: str | None) -> str:
    """拉取源文件原文。失败抛 RuntimeError（带可排障的信息）。"""
    url = source_url(version)
    req = urllib.request.Request(url, headers={"Accept": RAW_ACCEPT})
    # 公开仓库不需要令牌；带上可提高速率上限，也支持私有仓库
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", "json-repo-schema-sync")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise RuntimeError(
            f"拉取 v{version} 失败 HTTP {e.code}: {url}\n{detail}"
        ) from e
    except Exception as e:  # noqa: BLE001 - 网络层异常统一转成可读信息
        raise RuntimeError(f"拉取 v{version} 异常: {e}") from e

    # GitHub 在文件不存在时可能返回 HTML 错误页，必须挡在 parse 之前，
    # 否则会变成一个难以定位的 JSONDecodeError。
    if not body.lstrip().startswith("{"):
        raise RuntimeError(
            f"v{version} 内容不是 JSON（前 120 字符）: {body[:120]!r}"
        )

    try:
        json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"v{version} 不是合法 JSON: {e}") from e

    return body


def write_if_changed(path: str, content: str, dry_run: bool) -> bool:
    """写入文件；内容一致则不动。返回是否发生了变更。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            if f.read() == content:
                return False

    if dry_run:
        print(f"  [dry-run] 将写入 {path}")
        return True

    # 源文件末尾可能有/没有换行；统一补一个，避免无意义的 diff 抖动
    if not content.endswith("\n"):
        content += "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  已写入 {path}（{len(content)} 字节）")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="镜像 artifact-pipeline 的加固 schema")
    ap.add_argument(
        "--version",
        type=int,
        action="append",
        help="只同步指定版本（可重复）。缺省自动发现 configs/ 下所有 v<N>",
    )
    ap.add_argument("--dry-run", action="store_true", help="只报告不写文件")
    args = ap.parse_args()

    token = os.environ.get("GH_SCHEMA_SOURCE_TOKEN")

    print(f"源: {SOURCE_OWNER}/{SOURCE_REPO}@{SOURCE_REF}")
    if not token:
        print("提示: 未设置 GH_SCHEMA_SOURCE_TOKEN，按公开仓库匿名拉取")

    # ── 决定要同步哪些版本 ────────────────────────────────────────────
    if args.version:
        versions = args.version
        print(f"目标版本（命令行指定）: {versions}")
    elif SUPPORTED_VERSIONS is not None:
        versions = SUPPORTED_VERSIONS
        print(f"目标版本（常量）: {versions}")
    else:
        try:
            versions = discover_versions(token)
            print(f"目标版本（自动发现）: {versions}")
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ 自动发现失败，退回保守清单 {FALLBACK_VERSIONS}", file=sys.stderr)
            print(f"   原因: {e}", file=sys.stderr)
            versions = FALLBACK_VERSIONS
        if not versions:
            print("⚠️ 自动发现到 0 个版本，退回保守清单", file=sys.stderr)
            versions = FALLBACK_VERSIONS

    changed = False
    for v in versions:
        print(f"- v{v}")
        try:
            body = fetch(v, token)
        except RuntimeError as e:
            print(f"  ✘ {e}", file=sys.stderr)
            return 1

        out_path = os.path.join(OUT_DIR_TEMPLATE.format(version=v), OUT_FILE)
        if write_if_changed(out_path, body, args.dry_run):
            changed = True
        else:
            print("  无变化")

    # ── 索引文件：告诉 App「镜像里有哪些版本」────────────────────────
    # 为什么需要：App 原先靠 MAX_SUPPORTED_SCHEMA_VERSION 常量决定渲染几个
    # tab，那是「App 支持几个」而非「实际存在几个」。有了索引，App 可以
    # 渲染实际存在的卡片，而不用每次加卡片都发新版 App。
    index_path = os.path.join("backend", "apk_schema", "index.json")
    index_body = json.dumps(
        {"versions": sorted(versions)},
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    if write_if_changed(index_path, index_body, args.dry_run):
        changed = True

    print("有变更" if changed else "全部无变化")
    return 0


if __name__ == "__main__":
    sys.exit(main())
