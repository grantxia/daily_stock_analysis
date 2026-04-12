#!/usr/bin/env python3
"""
Scan industry meeting/policy events from public news and push summary to Feishu.
Primary source: Tavily.
Optional classifier: LiteLLM when LITELLM_MODEL is configured.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MEETING_KEYWORDS = (
    "meeting",
    "forum",
    "conference",
    "roundtable",
    "briefing",
    "symposium",
    "policy",
    "seminar",
    "\u5ea7\u8c08\u4f1a",
    "\u5439\u98ce\u4f1a",
    "\u53d1\u5e03\u4f1a",
    "\u5de5\u4f5c\u4f1a\u8bae",
    "\u8bba\u575b",
    "\u7814\u8ba8\u4f1a",
    "\u653f\u7b56",
)

POSITIVE_KEYWORDS = (
    "support",
    "promote",
    "boost",
    "accelerate",
    "pilot",
    "innovation",
    "growth",
    "\u63a8\u8fdb",
    "\u4fc3\u8fdb",
    "\u652f\u6301",
    "\u63d0\u632f",
    "\u843d\u5730",
    "\u589e\u957f",
)

NEGATIVE_KEYWORDS = (
    "tighten",
    "penalty",
    "risk",
    "decline",
    "concern",
    "\u6574\u6cbb",
    "\u5904\u7f5a",
    "\u6536\u7d27",
    "\u98ce\u9669",
    "\u4e0b\u6ed1",
)


@dataclass
class EventItem:
    industry: str
    title: str
    url: str
    published_at: str
    summary: str
    tone: str
    confidence: float
    impact_horizon: str
    rationale: str
    raw_score: float


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _split_csv(value: str) -> List[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def _post_json(url: str, payload: dict, timeout: int = 20) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = Request(
        url=url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body)


def _call_tavily(query: str, api_key: str, days: int, max_results: int) -> List[dict]:
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "advanced",
        "topic": "news",
        "max_results": max_results,
        "days": days,
        "include_answer": False,
        "include_raw_content": False,
    }
    result = _post_json("https://api.tavily.com/search", payload, timeout=25)
    return result.get("results", []) or []


def _heuristic_assess(text: str) -> Tuple[str, float, str, str, float]:
    pos = sum(1 for k in POSITIVE_KEYWORDS if k in text)
    neg = sum(1 for k in NEGATIVE_KEYWORDS if k in text)
    is_meeting = any(k in text for k in MEETING_KEYWORDS)
    upcoming_hint = bool(
        re.search(
            r"(will|upcoming|next week|this week|to be held|\u5373\u5c06|\u5c06\u4e8e|\u53ec\u5f00)",
            text,
            flags=re.I,
        )
    )

    raw_score = float(pos - neg) + (1.0 if is_meeting else 0.0) + (0.4 if upcoming_hint else 0.0)
    if raw_score >= 1.1:
        tone = "positive"
    elif raw_score <= -0.8:
        tone = "negative"
    else:
        tone = "neutral"

    confidence = 0.45 + min(0.40, 0.08 * (abs(pos - neg) + (1 if is_meeting else 0)))
    confidence = max(0.40, min(0.92, confidence))

    if upcoming_hint:
        horizon = "short(1-2w)"
    elif "plan" in text.lower() or "\u89c4\u5212" in text:
        horizon = "medium(1-3m)"
    else:
        horizon = "short(1-4w)"

    rationale = f"pos={pos}, neg={neg}, meeting={is_meeting}, upcoming={upcoming_hint}"
    return tone, confidence, horizon, rationale, raw_score


def _try_litellm_assess(title: str, summary: str) -> Optional[Tuple[str, float, str, str]]:
    model = (os.getenv("LITELLM_MODEL") or "").strip()
    if not model:
        return None
    try:
        import litellm  # type: ignore
    except Exception:
        return None

    prompt = (
        "Classify this event and return strict JSON with keys: "
        "tone(positive|neutral|negative), confidence(0-1), impact_horizon(short|medium|long), rationale.\n"
        f"Title: {title}\nSummary: {summary[:500]}"
    )
    try:
        resp = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=180,
            timeout=20,
        )
        content = resp["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", content, flags=re.S)
        if not match:
            return None
        data = json.loads(match.group(0))
        tone = str(data.get("tone", "neutral")).lower()
        confidence = float(data.get("confidence", 0.6))
        horizon = str(data.get("impact_horizon", "short")).lower()
        rationale = str(data.get("rationale", "")).strip()[:100]
        if tone not in {"positive", "neutral", "negative"}:
            tone = "neutral"
        confidence = max(0.35, min(0.95, confidence))
        if horizon not in {"short", "medium", "long"}:
            horizon = "short"
        return tone, confidence, horizon, rationale
    except Exception:
        return None


def _render_markdown(items: List[EventItem], lookback_days: int) -> str:
    lines = [
        "# Industry Meeting / Policy Event Scan",
        f"- Generated: {_now_str()}",
        f"- Lookback days: {lookback_days}",
        f"- Events: {len(items)}",
        "",
    ]
    for idx, it in enumerate(items, start=1):
        lines.append(f"## {idx}. [{it.title}]({it.url})")
        lines.append(f"- Industry: {it.industry}")
        lines.append(f"- Published: {it.published_at or 'unknown'}")
        lines.append(f"- Tone: {it.tone} | Confidence: {it.confidence:.2f} | Horizon: {it.impact_horizon}")
        lines.append(f"- Rationale: {it.rationale}")
        if it.summary:
            lines.append(f"- Summary: {it.summary[:180]}")
        lines.append("")
    return "\n".join(lines)


def _send_feishu_text(webhook_url: str, text: str) -> bool:
    payload = {"msg_type": "text", "content": {"text": text}}
    try:
        _post_json(webhook_url, payload, timeout=15)
        return True
    except Exception as exc:
        print(f"[event_scan] feishu push failed: {exc}")
        return False


def main() -> int:
    industries = _split_csv(
        os.getenv("INDUSTRY_WHITELIST", "semiconductor,ai,electric equipment,biotech,advanced manufacturing")
    )
    lookback_days = int(os.getenv("EVENT_LOOKBACK_DAYS", "14"))
    max_results = int(os.getenv("EVENT_MAX_RESULTS_PER_INDUSTRY", "6"))
    min_conf = float(os.getenv("EVENT_CONFIDENCE_MIN", "0.60"))
    top_n = int(os.getenv("EVENT_TOP_N", "12"))

    tavily_keys = _split_csv(os.getenv("TAVILY_API_KEYS", ""))
    if not tavily_keys:
        print("[event_scan] no TAVILY_API_KEYS configured")
        return 1

    all_items: List[EventItem] = []
    first_key = tavily_keys[0]

    for industry in industries:
        query = (
            f"{industry} meeting OR policy briefing OR roundtable OR conference OR "
            "\u5ea7\u8c08\u4f1a OR \u5439\u98ce\u4f1a OR \u53d1\u5e03\u4f1a latest"
        )
        try:
            rows = _call_tavily(query, api_key=first_key, days=lookback_days, max_results=max_results)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            print(f"[event_scan] tavily failed for {industry}: {exc}")
            continue

        for row in rows:
            title = str(row.get("title") or "").strip()
            url = str(row.get("url") or "").strip()
            content = str(row.get("content") or "").strip()
            published_at = str(row.get("published_date") or row.get("publishedAt") or "").strip()
            text = f"{title}\n{content}"
            if not any(k in text for k in MEETING_KEYWORDS):
                continue

            ai_result = _try_litellm_assess(title, content)
            if ai_result:
                tone, confidence, horizon, rationale = ai_result
                _, _, _, _, raw_score = _heuristic_assess(text)
            else:
                tone, confidence, horizon, rationale, raw_score = _heuristic_assess(text)

            if confidence < min_conf:
                continue

            all_items.append(
                EventItem(
                    industry=industry,
                    title=title or "(no title)",
                    url=url or "https://tavily.com",
                    published_at=published_at,
                    summary=content[:260],
                    tone=tone,
                    confidence=confidence,
                    impact_horizon=horizon,
                    rationale=rationale,
                    raw_score=raw_score,
                )
            )

    all_items.sort(key=lambda x: (x.confidence, x.raw_score), reverse=True)
    all_items = all_items[:top_n]

    reports_dir = Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / "event_scan_latest.json"
    md_path = reports_dir / "event_scan_latest.md"

    payload = {
        "generated_at": _now_str(),
        "lookback_days": lookback_days,
        "industry_count": len(industries),
        "event_count": len(all_items),
        "events": [asdict(i) for i in all_items],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(all_items, lookback_days), encoding="utf-8")
    print(f"[event_scan] generated {json_path} with {len(all_items)} events")

    feishu_webhook = (os.getenv("FEISHU_WEBHOOK_URL") or "").strip()
    if feishu_webhook:
        tone_counts = {"positive": 0, "neutral": 0, "negative": 0}
        industry_count = {}
        for item in all_items:
            tone_counts[item.tone] = tone_counts.get(item.tone, 0) + 1
            industry_count[item.industry] = industry_count.get(item.industry, 0) + 1

        top_industries = sorted(industry_count.items(), key=lambda x: x[1], reverse=True)[:4]
        top_industry_text = ",".join([f"{name}({cnt})" for name, cnt in top_industries]) or "-"

        lines = [
            f"\u3010\u884c\u4e1a\u4e8b\u4ef6\u626b\u63cf\u3011\u5171{len(all_items)}\u6761\uff08\u8fd1{lookback_days}\u5929\uff09",
            f"- \u91cd\u70b9\u884c\u4e1a\uff1a{top_industry_text}",
            (
                f"- \u504f\u5229\u597d\uff1a{tone_counts.get('positive', 0)}\u6761\uff1b"
                f"\u4e2d\u6027\uff1a{tone_counts.get('neutral', 0)}\u6761\uff1b"
                f"\u504f\u5229\u7a7a\uff1a{tone_counts.get('negative', 0)}\u6761"
            ),
        ]
        for i, item in enumerate(all_items[:3], start=1):
            title = item.title.replace("\n", " ").strip()
            lines.append(
                f"{i}. [{item.industry}] {title[:26]} | {item.tone} | {item.confidence:.2f}"
            )
        _send_feishu_text(feishu_webhook, "\n".join(lines))

    return 0


if __name__ == "__main__":
    sys.exit(main())
