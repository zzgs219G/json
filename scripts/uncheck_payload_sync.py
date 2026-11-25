#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
去签名校验载荷镜像同步
======================

职责：把 artifact-pipeline 仓库里的去签名校验载荷拉到本仓库的
backend/uncheck/ 下，供 ApkForge 的 PayloadRepo 下载。

为什么要镜像
------------
ApkForge 端刻意不直连源仓库：
  · 源仓库的可见性不由本工程控制（旧名 apk-protect-action 已转私有，
    匿名拉取 404 就是前车之鉴）；
  · 这些载荷会被注入到别人的 App 里执行，分发链路越少越可控。
本仓库（zzgs219/json）本来就是公开镜像站，App 只需知道这一个地址。

（与 scripts/apk_schema_sync.py 同一套理由，那边有更长的说明。）

⚠️ 为什么必须由脚本算 sha256
----------------------------
PayloadRepo 对每个文件做**严格** sha256 校验，不过就整包丢弃。
index.json 原先是人手填的：64 位十六进制串错一个字符的症状是
「下载成功 → 校验失败 → 功能不可用」，而报错只说「校验失败」，
看不出是填错了还是传输坏了。让脚本算，这类错误结构上不可能发生。

⚠️ 为什么 version 由内容派生，而不是手写
----------------------------------------
PayloadRepo 按 filesDir/uncheck/<version>/ 缓存，同版本不重复下载。
version 不跟内容变，用户就会一直用旧载荷 —— 2026-11 加 native 层时
就是这么漏的：线上 boot.dex 还停在无 native 支持的旧版，App 那边
照常「载荷就绪」，实际 native 层永远不生效。

内容派生 = 改了必变，不依赖任何人记得手改。

⚠️ 四个文件全是必需的
---------------------
不含 native 层的载荷对本工具基本没用：Java 层只能拦「通过
PackageManager 问签名」，而 App 自己 ZipFile(base.apk) 读签名块、
或 native so 直接解析 Signing Block 这两条路径完全绕过它。
缺 .so 就直接失败，不产出一个「装上去才发现没效果」的索引。

用法：
    python3 scripts/uncheck_payload_sync.py
    python3 scripts/uncheck_payload_sync.py --dry-run

依赖：仅标准库（urllib），无需 pip install。

退出码：
    0  成功（可能有/没有变更）
    1  拉取失败 / 内容不合法 / 缺必需文件
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

# 源仓库坐标。owner/repo 只出现在本脚本里，不进 APK。
SOURCE_OWNER = "zzgs219G"
SOURCE_REPO = "artifact-pipeline"
SOURCE_REF = "main"

# Contents API 走 raw 媒体类型可直接拿文件原文（二进制安全）
RAW_ACCEPT = "application/vnd.github.raw"

OUT_DIR = os.path.join("backend", "uncheck")
INDEX_REL = os.path.join(OUT_DIR, "index.json")

DEX_MAGIC = b"dex\n"
ELF_MAGIC = b"\x7fELF"

# (源路径, 落盘文件名, 期望魔数, 说明)
#
# ⚠️ 落盘必须**扁平**放在 backend/uncheck/ 下。
#    PayloadRepo 拼下载地址是 `$BASE/$name`（见下方 FILE_LAYOUT 注释），
#    放成 lib/libusign_arm64.so 的话 URL 会变成 .../lib/... 而 App 只请求
#    .../libusign_arm64.so → 404 → 载荷获取失败。
FILES = [
    ("scripts/uncheck/payload/boot.dex",
     "boot.dex", DEX_MAGIC, "引导器 SigFix + SigFixApp（进 classes{N+1}.dex）"),
    ("scripts/uncheck/payload/payload.dex",
     "payload.dex", DEX_MAGIC, "实现类 SigFixImpl（进 assets，BootClassLoader 加载）"),
    ("scripts/uncheck/payload/lib/libusign_arm64.so",
     "libusign_arm64.so", ELF_MAGIC, "native APK 路径重定向 arm64"),
    ("scripts/uncheck/payload/lib/libusign_armv7.so",
     "libusign_armv7.so", ELF_MAGIC, "native APK 路径重定向 armv7"),
]


def source_url(rel: str) -> str:
    return (
        f"https://api.github.com/repos/{SOURCE_OWNER}/{SOURCE_REPO}"
        f"/contents/{rel}?ref={SOURCE_REF}"
    )


def fetch_bytes(rel: str, token: str | None) -> bytes:
    """拉取源文件原始字节。失败抛 RuntimeError（带可排障信息）。"""
    url = source_url(rel)
    req = urllib.request.Request(url, headers={"Accept": RAW_ACCEPT})
    # 公开仓库不需要令牌；带上可提高速率上限，也兼容源仓库转私有的情况
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", "json-repo-uncheck-sync")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001 - 错误体读不到不影响主判据
            pass
        raise RuntimeError(
            f"拉取 {rel} 失败 HTTP {e.code}\n  {url}\n  {detail}"
        ) from e
    except Exception as e:  # noqa: BLE001 - 网络层异常统一转成可读信息
        raise RuntimeError(f"拉取 {rel} 异常: {e}") from e


def verify(rel: str, data: bytes, magic: bytes) -> None:
    """校验长度与魔数。

    魔数是这里唯一能低成本发现「拿到的是 HTML 错误页」的手段：
    源仓库转私有后 GitHub 会回 200 + 一段 HTML（或 404 页），
    urlopen 不报错，写下去就是一个 200 字节的假 dex ——
    而 App 端要到运行时才炸，且报错完全指不到这里。
    """
    if not data:
        raise RuntimeError(f"{rel} 拉到 0 字节")
    if not data.startswith(magic):
        raise RuntimeError(
            f"{rel} 内容不是预期格式：前 16 字节 {data[:16]!r}，"
            f"期望以 {magic!r} 开头。\n"
            f"  常见原因：源仓库不是公开的（拿到的是错误页），或源文件被替换。"
        )


def write_if_changed(path: str, data: bytes, dry_run: bool) -> bool:
    """写入文件；内容一致则不动（避免无意义 diff 抖动）。返回是否发生变更。"""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    if os.path.exists(path):
        with open(path, "rb") as f:
            if f.read() == data:
                return False

    if dry_run:
        print(f"  [dry-run] 将写入 {path}（{len(data)} 字节）")
        return True

    with open(path, "wb") as f:
        f.write(data)
    print(f"  已写入 {path}（{len(data)} 字节）")
    return True


def build_index(items: list[tuple[str, bytes]]) -> tuple[str, bytes]:
    """生成 (version, index.json 字节)。

    version 由「文件名 + 内容」的合并摘要派生，取前 12 位十六进制。
    PayloadRepo 只把它当缓存目录名与展示串用，不解析语义。
    """
    files: dict[str, dict[str, object]] = {}
    h = hashlib.sha256()
    for name, data in sorted(items, key=lambda x: x[0]):
        files[name] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }
        # 名字也进摘要：改了文件名（比如换 ABI 命名）同样必须换 version
        h.update(name.encode("utf-8"))
        h.update(b"\x00")
        h.update(data)

    version = h.hexdigest()[:12]
    body = json.dumps(
        {"version": version, "files": files},
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    return version, body.encode("utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="镜像 artifact-pipeline 的去签名校验载荷")
    ap.add_argument("--dry-run", action="store_true", help="只报告不写文件")
    args = ap.parse_args()

    token = os.environ.get("GH_UNCHECK_SOURCE_TOKEN")

    print(f"源: {SOURCE_OWNER}/{SOURCE_REPO}@{SOURCE_REF}")
    if not token:
        print("提示: 未设置 GH_UNCHECK_SOURCE_TOKEN，按公开仓库匿名拉取")

    fetched: list[tuple[str, bytes]] = []
    for src, dst, magic, desc in FILES:
        print(f"- {dst}  （{desc}）")
        try:
            data = fetch_bytes(src, token)
            verify(src, data, magic)
        except RuntimeError as e:
            print(f"  ✘ {e}", file=sys.stderr)
            return 1
        fetched.append((dst, data))
        print(f"  ok {len(data)} 字节")

    version, index_bytes = build_index(fetched)

    # ── 落盘 ──────────────────────────────────────────────────────────
    changed = False
    for name, data in fetched:
        if write_if_changed(os.path.join(OUT_DIR, name), data, args.dry_run):
            changed = True
    if write_if_changed(INDEX_REL, index_bytes, args.dry_run):
        changed = True

    # ── 报告 ──────────────────────────────────────────────────────────
    print()
    print(f"version = {version}   （由内容派生：改载荷必变，缓存自动失效）")
    for name, data in fetched:
        print(f"  {name:<22} {len(data):>9} B")
    print(f"  {'index.json':<22} {len(index_bytes):>9} B")
    print("有变更" if changed else "全部无变化")
    return 0


if __name__ == "__main__":
    sys.exit(main())
