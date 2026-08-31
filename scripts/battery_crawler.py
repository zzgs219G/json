#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
极客湾 5G 续航排行榜爬虫
========================

职责：抓取 socpk.com/batlife 页面，从其前端 JS bundle 中提取加密榜单数据，
解密后产出结构化 JSON，供 App 端 / WebView 页面拉取展示。

产出物：backend/battery/battery_rank.json
    {
      "generatedAt": "...",       # 抓取时间（UTC ISO8601）
      "source": "...",            # 数据来源页面
      "total": 78,                # 机型总数
      "axis": [0,180,...,900],    # 横轴刻度（小时），与官网一致
      "items": [                  # 按续航时长降序
        {
          "rank": 1,
          "brand": "荣耀",         # 品牌（对应官网配色表）
          "model": "WIN RT",       # 机型
          "hours": 726,            # 三小时续航折算时长
          "osVersion": "...",      # 测试时系统版本
          "batteryMah": null,      # 电池容量（可能为 null）
          "batteryWh": 36.88,      # 电池能量
          "videoUrl": "https://..."# B站全程测试录像（可能为 "NA"）
        }, ...
      ]
    }

实现原理（已实测验证，2026-08）：
    1. 官网为 Vue 单页应用，榜单数据构建时以「base64 + 密钥循环 XOR」加密后
       内嵌进 JS bundle（解密函数形如 Hl(e,t)，JSON.parse(decrypt(atob(e)))）。
    2. 本脚本不做任何硬编码：每次运行都从页面 HTML 提取当前 bundle 文件名，
       再用正则从 bundle 中现场提取 Hl(...) 的两个参数（密文 + 密钥），
       只要官网不更换加密算法即可自愈式解析。
    3. 榜单字段为 7 元素数组：[品牌, 机型, 时长h, 系统版本, mAh, Wh, 录像链接]，
       含义由官网 BatteryPage 渲染代码逆向确认（含 mAh 时显示 mAh(Wh)，录像
       链接为 "NA" 表示未上传）。

用法：
    python3 scripts/battery_crawler.py                       # 输出 backend/battery/battery_rank.json
    python3 scripts/battery_crawler.py --out /tmp/x.json     # 指定输出路径
"""

import argparse
import base64
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_PAGE = "https://www.socpk.com/batlife"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "backend" / "battery" / "battery_rank.json"

# 横轴刻度（小时），与官网 BatteryPage 中 n=[0,180,360,540,720,900] 一致
AXIS_HOURS = [0, 180, 360, 540, 720, 900]

# 品牌配色（供渲染端参考，随 JSON 一并输出；来源：官网 BatteryPage 品牌色表）
BRAND_COLORS = {
    "苹果": "#732EE3", "vivo": "#3E5BF4", "小米": "#F46400", "OPPO": "#00CA81",
    "iQOO": "#F4AC01", "红米": "#DF003E", "一加": "#E40027", "真我": "#F6C318",
    "Realme": "#F6C318", "努比亚": "#FF2E4C", "谷歌": "#4285F4", "荣耀": "#01AEE1",
    "华为": "#E12D2C", "红魔": "#E61939", "三星": "#3E5BF4",
}
BRAND_COLOR_FALLBACK = "#1ED76D"

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
    """从 bundle 中提取全部 Hl(`base64`,`key`) 调用的（密文, 密钥）对。

    bundle 内可能有多个加密块（榜单 rankings / 曲线 curves），逐个尝试解密，
    取能解析出 battery50 字段的那个。
    """
    return re.findall(r'Hl\(`([^`]+)`,`([^`]+)`\)', bundle)


def decrypt(b64_payload: str, key: str) -> bytes:
    data = base64.b64decode(b64_payload)
    kb = key.encode("utf-8")
    return bytes(b ^ kb[i % len(kb)] for i, b in enumerate(data))


def parse_battery50(session: requests.Session):
    html = fetch(session, BASE_PAGE)
    bundle_url = extract_bundle_url(html)
    print(f"[battery] bundle: {bundle_url}")
    bundle = fetch(session, bundle_url)

    for b64_payload, key in extract_payloads(bundle):
        try:
            obj = json.loads(decrypt(b64_payload, key))
        except Exception:
            continue
        if isinstance(obj, dict) and "battery50" in obj:
            print(f"[battery] 解密成功，key={key!r}，含字段: {sorted(obj.keys())}")
            return obj["battery50"], key

    raise RuntimeError("bundle 中所有加密块均无法解出 battery50，官网加密方案可能已变更")


def to_items(raw) -> list:
    """7 元素数组 -> 结构化记录，并按续航时长降序排名（与官网 sort 一致）。"""
    items = []
    for brand, model, hours, os_version, mah, wh, video in raw:
        items.append({
            "brand": brand,
            "model": model,
            "hours": hours,
            "osVersion": os_version,
            "batteryMah": mah if mah else None,
            "batteryWh": wh,
            "videoUrl": None if video == "NA" else video,
            "brandColor": BRAND_COLORS.get(brand, BRAND_COLOR_FALLBACK),
        })
    items.sort(key=lambda x: x["hours"], reverse=True)
    for i, it in enumerate(items, 1):
        it["rank"] = i
    return items


def main():
    ap = argparse.ArgumentParser(description="极客湾续航排行榜爬虫")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出 JSON 路径")
    args = ap.parse_args()

    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    raw, key = parse_battery50(session)
    items = to_items(raw)
    out = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": BASE_PAGE,
        "decryptKey": key,
        "total": len(items),
        "axis": AXIS_HOURS,
        "brandColors": BRAND_COLORS,
        "brandColorFallback": BRAND_COLOR_FALLBACK,
        "items": items,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[battery] 共 {len(items)} 款机型，已写入 {out_path}")
    top3 = [(it["rank"], it["brand"], it["model"], it["hours"]) for it in items[:3]]
    print(f"[battery] Top3: {top3}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[battery] 爬取失败: {e}", file=sys.stderr)
        sys.exit(1)
