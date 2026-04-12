#!/usr/bin/env python3
"""
Generate a daily event review brief from event_scan output and push to Feishu.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from urllib.request import Request, urlopen


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


def _load_events(path: Path) -> List[dict]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return payload.get("events", []) or []


def _event_status_from_text(title: str, summary: str) -> str:
    text = f"{title} {summary}".lower()
    held_keys = (
        "held",
        "concluded",
        "\u53d1\u5e03",  # 发布
        "\u53ec\u5f00",  # 召开
        "\u843d\u5730",  # 落地
        "\u516c\u5e03",  # 公布
    )
    upcoming_keys = (
        "will",
        "upcoming",
        "\u5373\u5c06",  # 即将
        "\u5c06\u4e8e",  # 将于
        "\u62df\u4e8e",  # 拟于
    )
    if any(k in text for k in held_keys):
        return "post_event"
    if any(k in text for k in upcoming_keys):
        return "upcoming"
    return "tracking"


def _safe_title(title: str) -> str:
    title = re.sub(r"\s+", " ", (title or "").strip())
    return title[:36]


def main() -> int:
    event_file = Path("reports/event_scan_latest.json")
    events = _load_events(event_file)
    if not events:
        print("[event_review] no events to review")
        return 0

    tone_count: Dict[str, int] = {"positive": 0, "neutral": 0, "negative": 0}
    status_count: Dict[str, int] = {"upcoming": 0, "post_event": 0, "tracking": 0}
    industry_count: Dict[str, int] = {}

    for ev in events:
        tone = str(ev.get("tone", "neutral")).lower()
        tone_count[tone] = tone_count.get(tone, 0) + 1
        industry = str(ev.get("industry", "")).strip() or "unknown"
        industry_count[industry] = industry_count.get(industry, 0) + 1
        status = _event_status_from_text(str(ev.get("title", "")), str(ev.get("summary", "")))
        status_count[status] = status_count.get(status, 0) + 1

    top_industries = sorted(industry_count.items(), key=lambda x: x[1], reverse=True)[:4]
    top_industry_text = ",".join([f"{name}({cnt})" for name, cnt in top_industries]) or "-"

    top_positive = [ev for ev in events if str(ev.get("tone", "")).lower() == "positive"]
    top_positive = sorted(top_positive, key=lambda x: float(x.get("confidence", 0.0)), reverse=True)[:3]

    lines = [
        "\u3010\u4f1a\u8bae\u590d\u76d8\u8ddf\u8e2a\u3011\u65e5\u62a5",
        f"- \u7edf\u8ba1\u65f6\u95f4\uff1a{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- \u4e8b\u4ef6\u603b\u91cf\uff1a{len(events)}",
        f"- \u91cd\u70b9\u884c\u4e1a\uff1a{top_industry_text}",
        (
            f"- \u6027\u8d28\uff1a\u504f\u5229\u597d{tone_count.get('positive', 0)} | "
            f"\u4e2d\u6027{tone_count.get('neutral', 0)} | "
            f"\u504f\u5229\u7a7a{tone_count.get('negative', 0)}"
        ),
        (
            f"- \u9636\u6bb5\uff1a\u4f1a\u524d{status_count.get('upcoming', 0)} | "
            f"\u4f1a\u540e{status_count.get('post_event', 0)} | "
            f"\u8ddf\u8e2a\u4e2d{status_count.get('tracking', 0)}"
        ),
    ]

    if top_positive:
        lines.append("- \u9ad8\u4f18\u5148\u7ea7\u4e8b\u4ef6:")
        for idx, ev in enumerate(top_positive, start=1):
            lines.append(
                f"  {idx}. [{ev.get('industry', 'unknown')}] "
                f"{_safe_title(str(ev.get('title', '')))} "
                f"({float(ev.get('confidence', 0.0)):.2f})"
            )

    lines.append("\u63d0\u793a\uff1a\u590d\u76d8\u4ee5\u516c\u5f00\u4fe1\u606f\u4e3a\u57fa\u7840\uff0c\u4ec5\u4f9b\u7814\u7a76\u53c2\u8003\u3002")
    text = "\n".join(lines)

    reports_dir = Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "event_review_latest.md").write_text(text, encoding="utf-8")
    (reports_dir / "event_review_latest.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "total_events": len(events),
                "tone_count": tone_count,
                "status_count": status_count,
                "top_industries": top_industries,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    webhook = (os.getenv("FEISHU_WEBHOOK_URL") or "").strip()
    if webhook:
        try:
            _send_feishu_text(webhook, text)
        except Exception as exc:
            print(f"[event_review] feishu push failed: {exc}")
            return 1

    print("[event_review] report generated and pushed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
