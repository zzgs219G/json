#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
极客湾（socpk）芯片综合性能排行榜爬虫
====================================

职责：抓取 socpk.com/allperf 页面，从其前端 JS bundle 中提取加密榜单数据，
解密后产出结构化 JSON，供 App 端 / WebView 页面拉取展示。

产出物：backend/socpk/allperf/socpk_allperf.json
    {
      "generatedAt": "...",         # 抓取时间（UTC ISO8601）
      "source": "...",              # 数据来源页面
      "total": 158,                 # 芯片总数
      "axis": [0,125,250,375,500],  # 横轴刻度（分），官网 RankingBarTable 动态生成 max/4*n
      "notice": "...",              # 官网榜单说明（CPU权重70%, GPU权重30%）
      "modes": {...},               # 官网各筛选模式的 max（all/phone/品牌维度）
      "items": [                    # 按分数降序
        {
          "rank": 1,
          "brand": "苹果",          # 品牌（对应官网配色表）
          "chip": "M4 (4+6)",       # 芯片型号
          "score": 452.59,          # 综合性能分（以骁龙865为基准的相对分）
          "brandColor": "#732EE3"
        }, ...
      ]
    }

实现原理（已实测验证，2026-09）：
    1. 与 socpk_battery.py 同源：官网为 Vue 单页应用，榜单数据构建时以
       「base64 + 密钥循环 XOR」加密后内嵌进 JS bundle（解密函数 Hl(e,t)）。
    2. allPerf 与 battery50 在同一加密 JSON 对象中，本脚本解同一密文块、
       取 allPerf 字段。不做任何硬编码：bundle 文件名带 hash 自动跟随，
       加密参数用正则现场提取。
    3. 榜单字段为 3 元素数组：[品牌, 芯片, 分数]，含义由官网 RankingBarTable
       渲染代码逆向确认（valueIndex=2，轴刻度 = mode.max/4 * n，n∈[0,4]）。

用法：
    python3 scripts/socpk_allperf.py                       # 输出 backend/socpk/allperf/socpk_allperf.json
    python3 scripts/socpk_allperf.py --out /tmp/x.json     # 指定输出路径
"""

import argparse
import base64
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_PAGE = "https://www.socpk.com/allperf"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "backend" / "socpk" / "allperf" / "socpk_allperf.json"

# 官网各筛选模式的分数上限（来源：官网 modes 定义 ql({all:500,phone:300,apple:500,qualcomm:300,mediatek:300,huawei:150}, 1, 'cyan')）
# 轴刻度由 max/4 均分生成（RankingBarTable: Array.from({length:5},(_,n)=>mode.max/4*n)）
MODE_MAXES = {"all": 500, "phone": 300, "apple": 500, "qualcomm": 300, "mediatek": 300, "huawei": 150}

# 官网榜单说明文案（Jl.allperf.notice）
NOTICE = "CPU权重70% ,GPU权重30%。不包含功耗、AI、ISP基带性能。以骁龙865为基准"

# 品牌配色（来源：官网 Gl 品牌色表，与 socpk_battery.py 同表）
BRAND_COLORS = {
    "苹果": "#732EE3", "vivo": "#3E5BF4", "小米": "#F46400", "OPPO": "#00CA81",
    "iQOO": "#F4AC01", "红米": "#DF003E", "一加": "#E40027", "真我": "#F6C318",
    "Realme": "#F6C318", "努比亚": "#FF2E4C", "谷歌": "#4285F4", "荣耀": "#01AEE1",
    "华为": "#E12D2C", "红魔": "#E61939", "三星": "#3E5BF4",
}
BRAND_COLOR_FALLBACK = "#BBBBBB"  # 官网未知品牌灰 c(e)??{color:'#BBBBBB'}

# 官网「仅手机」模式排除的平板/非手机芯片关键字（Kl 数组：型号含这些词即非手机 SoC）
NON_PHONE_KEYWORDS = ("iPad", "M1", "M2", "M3", "M4", "M5")

TIMEOUT = 30
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def fetch(session: requests.Session, url: str) -> str:
    resp = session.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


def extract_bundle_url(html: str) -> str:
    """从页面 HTML 提取主 JS bundle 地址（带 hash，官网重建后自动跟随）。"""
    m = re.search(r'src="(/assets/index-[\w-]+\.js)"', html)
    if not m:
        raise RuntimeError("未能从页面 HTML 中定位 JS bundle 引用，官网结构可能已变更")
    return "https://www.socpk.com" + m.group(1)


def extract_payloads(bundle: str):
    """从 bundle 中提取全部 Hl(`base64`,`key`) 调用的（密文, 密钥）对。"""
    return re.findall(r'Hl\(`([^`]+)`,`([^`]+)`\)', bundle)


def decrypt(b64_payload: str, key: str) -> bytes:
    data = base64.b64decode(b64_payload)
    kb = key.encode("utf-8")
    return bytes(b ^ kb[i % len(kb)] for i, b in enumerate(data))


def parse_allperf(session: requests.Session):
    html = fetch(session, BASE_PAGE)
    bundle_url = extract_bundle_url(html)
    print(f"[socpk-allperf] bundle: {bundle_url}")
    bundle = fetch(session, bundle_url)

    for b64_payload, key in extract_payloads(bundle):
        try:
            obj = json.loads(decrypt(b64_payload, key))
        except Exception:
            continue
        if isinstance(obj, dict) and "allPerf" in obj:
            print(f"[socpk-allperf] 解密成功，key={key!r}，含字段: {sorted(obj.keys())}")
            return obj["allPerf"], key

    raise RuntimeError("bundle 中所有加密块均无法解出 allPerf，官网加密方案可能已变更")


def is_phone_chip(chip: str) -> bool:
    """官网「仅手机」模式（phoneOnly）过滤：型号含平板/PC 级芯片关键字即排除。"""
    upper = chip.upper()
    return not any(k.upper() in upper for k in NON_PHONE_KEYWORDS)


def build_axis(max_score: float) -> list:
    """官网轴刻度：Array.from({length:5},(_,n)=>max/4*n) → [0, max/4, max/2, max*3/4, max]"""
    return [round(max_score / 4 * n, 2) for n in range(5)]


def to_items(raw) -> list:
    """3 元素数组 -> 结构化记录，并按分数降序排名（与官网 sort 一致）。"""
    items = []
    for brand, chip, score in raw:
        items.append({
            "brand": brand,
            "chip": chip,
            "score": score,
            "phone": is_phone_chip(chip),   # 供 App/页面做「仅手机」筛选，免二次维护关键字表
            "brandColor": BRAND_COLORS.get(brand, BRAND_COLOR_FALLBACK),
        })
    items.sort(key=lambda x: x["score"], reverse=True)
    for i, it in enumerate(items, 1):
        it["rank"] = i
    return items


def main():
    ap = argparse.ArgumentParser(description="极客湾（socpk）芯片综合性能排行榜爬虫")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出 JSON 路径")
    args = ap.parse_args()

    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    raw, key = parse_allperf(session)
    items = to_items(raw)
    out = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": BASE_PAGE,
        "decryptKey": key,
        "total": len(items),
        "axis": build_axis(MODE_MAXES["all"]),
        "notice": NOTICE,
        "modes": MODE_MAXES,
        "brandColors": BRAND_COLORS,
        "brandColorFallback": BRAND_COLOR_FALLBACK,
        "items": items,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[socpk-allperf] 共 {len(items)} 款芯片，已写入 {out_path}")
    top3 = [(it["rank"], it["brand"], it["chip"], it["score"]) for it in items[:3]]
    print(f"[socpk-allperf] Top3: {top3}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[socpk-allperf] 爬取失败: {e}", file=sys.stderr)
        sys.exit(1)
