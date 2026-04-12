#!/usr/bin/env python3
"""
Build event-driven candidate stock pool from event_scan output,
then run existing stock-only analysis with fundamentals enabled.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Set
from urllib.request import Request, urlopen


def _split_csv(value: str) -> List[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def _parse_industry_stock_map(raw: str) -> Dict[str, List[str]]:
    """
    Format:
    semiconductor:688981,603986;biotech:300759,600276;power:300750,002594
    """
    mapping: Dict[str, List[str]] = {}
    for chunk in (raw or "").split(";"):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        industry, stocks = chunk.split(":", 1)
        codes = _split_csv(stocks)
        if industry.strip() and codes:
            mapping[industry.strip()] = codes
    return mapping


def _load_events(path: Path) -> List[dict]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return payload.get("events", []) or []


def _send_feishu_text(webhook_url: str, text: str) -> None:
    payload = {"msg_type": "text", "content": {"text": text}}
    data = json.dumps(payload).encode("utf-8")
    req = Request(
        url=webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=15):
        pass


def _ordered_unique(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def main() -> int:
    event_file = Path("reports/event_scan_latest.json")
    conf_min = float(os.getenv("EVENT_CONFIDENCE_MIN", "0.60"))
    max_stocks = int(os.getenv("VALUE_PICK_MAX_STOCKS", "12"))
    fallback_stocks = _split_csv(os.getenv("STOCK_LIST", "600519,300750,002594"))
    stock_map = _parse_industry_stock_map(os.getenv("INDUSTRY_STOCK_MAP", ""))

    events = _load_events(event_file)
    picked_industries: List[str] = []
    for ev in events:
        tone = str(ev.get("tone", "neutral")).lower()
        confidence = float(ev.get("confidence", 0.0))
        industry = str(ev.get("industry", "")).strip()
        if tone == "positive" and confidence >= conf_min and industry:
            picked_industries.append(industry)
    picked_industries = _ordered_unique(picked_industries)

    selected: List[str] = []
    seen: Set[str] = set()
    for ind in picked_industries:
        for code in stock_map.get(ind, []):
            if code not in seen:
                selected.append(code)
                seen.add(code)

    if not selected:
        selected = fallback_stocks[:]
    selected = selected[:max_stocks]
    if not selected:
        print("[event_value_pick] no candidates available")
        return 1

    feishu_webhook = (os.getenv("FEISHU_WEBHOOK_URL") or "").strip()
    if feishu_webhook:
        grouped_lines: List[str] = []
        if picked_industries:
            for ind in picked_industries:
                codes = [c for c in stock_map.get(ind, []) if c in selected]
                if codes:
                    grouped_lines.append(f"- {ind}: {','.join(codes)}")
        if not grouped_lines:
            grouped_lines.append(f"- fallback: {','.join(selected)}")

        intro = "\n".join(
            [
                f"\u3010\u4e8b\u4ef6\u9a71\u52a8\u5019\u9009\u6c60\u3011\u5171{len(selected)}\u53ea",
                (
                    f"- \u8986\u76d6\u884c\u4e1a\uff1a{','.join(picked_industries)}"
                    if picked_industries
                    else "- \u8986\u76d6\u884c\u4e1a\uff1a\u65e0\u9ad8\u7f6e\u4fe1\u4e8b\u4ef6\uff0c\u4f7f\u7528\u515c\u5e95\u6c60"
                ),
                *grouped_lines,
                "\u63d0\u793a\uff1a\u4ec5\u4e3a\u7814\u7a76\u5019\u9009\u6c60\uff0c\u4e0d\u6784\u6210\u6295\u8d44\u5efa\u8bae\u3002",
            ]
        )
        try:
            _send_feishu_text(feishu_webhook, intro)
        except Exception as exc:
            print(f"[event_value_pick] feishu pre-push failed: {exc}")

    env = os.environ.copy()
    env["STOCK_LIST"] = ",".join(selected)

    cmd = [sys.executable, "main.py", "--no-market-review", "--force-run"]
    print(f"[event_value_pick] run: {' '.join(cmd)}")
    print(f"[event_value_pick] STOCK_LIST={env['STOCK_LIST']}")
    proc = subprocess.run(cmd, env=env)
    return int(proc.returncode)


if __name__ == "__main__":
    sys.exit(main())
