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
        intro = (
            "Event-driven candidate pool prepared\n"
            f"Industries: {','.join(picked_industries) if picked_industries else 'fallback'}\n"
            f"Stocks: {','.join(selected)}"
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
