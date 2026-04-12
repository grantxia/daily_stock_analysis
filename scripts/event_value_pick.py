#!/usr/bin/env python3
"""
Build event-driven candidate stock pool from event_scan output,
apply undervaluation score threshold, then run stock-only analysis.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
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


def _safe_float(value: object) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class ValuationItem:
    code: str
    industry: str
    pe: Optional[float]
    pb: Optional[float]
    score_abs: float
    score_industry_pct: float
    score_final: float
    reason: str


def _calc_undervalue_score(pe: Optional[float], pb: Optional[float]) -> Tuple[float, str]:
    score = 50.0
    reasons: List[str] = []

    if pe is None or pe <= 0:
        score -= 8
        reasons.append("PE missing/invalid")
    else:
        if pe <= 12:
            score += 24
            reasons.append("PE<=12")
        elif pe <= 20:
            score += 14
            reasons.append("PE<=20")
        elif pe <= 35:
            score += 5
            reasons.append("PE<=35")
        elif pe > 60:
            score -= 16
            reasons.append("PE>60")

    if pb is None or pb <= 0:
        score -= 6
        reasons.append("PB missing/invalid")
    else:
        if pb <= 1.5:
            score += 20
            reasons.append("PB<=1.5")
        elif pb <= 2.5:
            score += 12
            reasons.append("PB<=2.5")
        elif pb <= 4:
            score += 4
            reasons.append("PB<=4")
        elif pb > 8:
            score -= 14
            reasons.append("PB>8")

    score = max(0.0, min(100.0, score))
    return score, ",".join(reasons) if reasons else "neutral"


def _fetch_realtime_pe_pb(code: str) -> Tuple[Optional[float], Optional[float]]:
    try:
        from data_provider import DataFetcherManager

        manager = DataFetcherManager()
        quote = manager.get_realtime_quote(code)
        if quote is None:
            return None, None
        pe = _safe_float(getattr(quote, "pe_ratio", None))
        pb = _safe_float(getattr(quote, "pb_ratio", None))
        return pe, pb
    except Exception:
        return None, None


def _percentile_score_low_better(value: Optional[float], peers: List[float]) -> float:
    """Return 0-100 score where lower value gets higher score."""
    if value is None or value <= 0:
        return 35.0
    clean = sorted([x for x in peers if x is not None and x > 0])
    if len(clean) < 2:
        return 60.0
    less_count = sum(1 for x in clean if x < value)
    pct = less_count / max(1, len(clean) - 1)  # 0 means cheapest, 1 means expensive
    return max(0.0, min(100.0, (1.0 - pct) * 100.0))


def main() -> int:
    event_file = Path("reports/event_scan_latest.json")
    conf_min = float(os.getenv("EVENT_CONFIDENCE_MIN", "0.60"))
    max_stocks = int(os.getenv("VALUE_PICK_MAX_STOCKS", "12"))
    undervalue_min = float(os.getenv("UNDERVALUE_SCORE_MIN", "55"))
    require_valuation_data = os.getenv("REQUIRE_VALUATION_DATA", "false").lower() == "true"
    industry_pct_weight = float(os.getenv("INDUSTRY_PERCENTILE_WEIGHT", "0.45"))
    industry_pct_weight = max(0.0, min(0.8, industry_pct_weight))
    abs_weight = 1.0 - industry_pct_weight

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

    raw_candidates: List[str] = []
    seen: Set[str] = set()
    code_industry: Dict[str, str] = {}
    for ind in picked_industries:
        for code in stock_map.get(ind, []):
            if code not in seen:
                raw_candidates.append(code)
                seen.add(code)
                code_industry[code] = ind

    if not raw_candidates:
        raw_candidates = fallback_stocks[:]
        for code in raw_candidates:
            code_industry.setdefault(code, "fallback")
    raw_candidates = raw_candidates[: max_stocks * 2]
    if not raw_candidates:
        print("[event_value_pick] no candidates available")
        return 1

    valuation_rows: List[ValuationItem] = []
    industry_pe_values: Dict[str, List[float]] = {}
    industry_pb_values: Dict[str, List[float]] = {}

    cached_quote: Dict[str, Tuple[Optional[float], Optional[float]]] = {}

    def _get_quote(code: str) -> Tuple[Optional[float], Optional[float]]:
        if code in cached_quote:
            return cached_quote[code]
        cached_quote[code] = _fetch_realtime_pe_pb(code)
        return cached_quote[code]

    # Build peer distribution for picked industries (industry-relative valuation).
    for ind in picked_industries:
        for code in stock_map.get(ind, []):
            pe, pb = _get_quote(code)
            if pe is not None and pe > 0:
                industry_pe_values.setdefault(ind, []).append(pe)
            if pb is not None and pb > 0:
                industry_pb_values.setdefault(ind, []).append(pb)

    for code in raw_candidates:
        ind = code_industry.get(code, "fallback")
        pe, pb = _get_quote(code)
        score_abs, reason = _calc_undervalue_score(pe, pb)
        pe_pct_score = _percentile_score_low_better(pe, industry_pe_values.get(ind, []))
        pb_pct_score = _percentile_score_low_better(pb, industry_pb_values.get(ind, []))
        score_industry_pct = (pe_pct_score * 0.6) + (pb_pct_score * 0.4)
        score_final = (score_abs * abs_weight) + (score_industry_pct * industry_pct_weight)
        valuation_rows.append(
            ValuationItem(
                code=code,
                industry=ind,
                pe=pe,
                pb=pb,
                score_abs=score_abs,
                score_industry_pct=score_industry_pct,
                score_final=score_final,
                reason=reason,
            )
        )

    passed = []
    for row in valuation_rows:
        has_data = (row.pe is not None and row.pe > 0) or (row.pb is not None and row.pb > 0)
        if require_valuation_data and not has_data:
            continue
        if row.score_final >= undervalue_min:
            passed.append(row)

    passed.sort(key=lambda x: x.score_final, reverse=True)
    selected = [x.code for x in passed][:max_stocks]

    # Fallback: avoid empty pool in unstable market data sessions.
    if not selected:
        valuation_rows.sort(key=lambda x: x.score_final, reverse=True)
        selected = [x.code for x in valuation_rows[: max(3, min(max_stocks, 6))]]

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

        score_preview = ", ".join([f"{x.code}:{x.score_final:.0f}" for x in passed[:5]]) or "none"
        intro = "\n".join(
            [
                f"\u3010\u4e8b\u4ef6\u9a71\u52a8+\u4f4e\u4f30\u503c\u5019\u9009\u6c60\u3011\u5171{len(selected)}\u53ea",
                (
                    f"- \u8986\u76d6\u884c\u4e1a\uff1a{','.join(picked_industries)}"
                    if picked_industries
                    else "- \u8986\u76d6\u884c\u4e1a\uff1a\u65e0\u9ad8\u7f6e\u4fe1\u4e8b\u4ef6\uff0c\u4f7f\u7528\u515c\u5e95\u6c60"
                ),
                (
                    f"- \u4f4e\u4f30\u503c\u9608\u503c\uff1a{undervalue_min:.0f} "
                    f"(\u901a\u8fc7 {len(passed)}/{len(valuation_rows)})"
                ),
                (
                    f"- \u8bc4\u5206\u6a21\u578b\uff1a\u7edd\u5bf9\u4f30\u503c{abs_weight:.2f} + "
                    f"\u884c\u4e1a\u5206\u4f4d{industry_pct_weight:.2f}"
                ),
                f"- \u5f97\u5206Top\uff1a{score_preview}",
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
