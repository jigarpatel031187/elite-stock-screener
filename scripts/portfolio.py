"""Portfolio-construction layer (critiques #3 and #9).

The screener grades names; this module grades the LIST:
  - sector concentration per tab ("17 tickers, one policy bet" detection),
  - pairwise 6m return correlation flags among top names,
  - volatility-scaled suggested weights under max-position / max-theme caps.

Suggested weights are a risk-budget illustration, not advice - they equalize
estimated risk contribution, they do not predict returns.
"""
from __future__ import annotations
import pandas as pd

from config import PORTFOLIO


def sector_concentration(rows: list) -> dict:
    counts: dict[str, int] = {}
    for r in rows:
        sec = r["fundamentals"].get("sector") or "Unknown"
        counts[sec] = counts.get(sec, 0) + 1
    total = max(1, len(rows))
    warnings = [f"{sec}: {n}/{total} names ({n / total:.0%}) - exceeds "
                f"{PORTFOLIO['max_sector_share']:.0%} theme cap; this is one "
                f"macro bet wearing {n} tickers"
                for sec, n in counts.items()
                if n / total > PORTFOLIO["max_sector_share"] and n >= 3]
    return {"counts": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
            "warnings": warnings}


def correlation_flags(rows: list, hists: dict[str, pd.DataFrame]) -> list[dict]:
    """Pairs of top names whose 6m daily returns co-move above the flag level."""
    w = PORTFOLIO["corr_window"]
    series = {}
    for r in rows[:15]:                       # top of the list is what gets bought
        h = hists.get(r["symbol"])
        if h is not None and len(h) >= w:
            series[r["symbol"]] = h["Close"].tail(w).pct_change().dropna()
    flags = []
    syms = list(series)
    for i in range(len(syms)):
        for j in range(i + 1, len(syms)):
            a, b = series[syms[i]].align(series[syms[j]], join="inner")
            if len(a) < 60:
                continue
            c = float(a.corr(b))
            if c >= PORTFOLIO["corr_flag"]:
                flags.append({"pair": [syms[i], syms[j]], "corr_6m": round(c, 2)})
    return sorted(flags, key=lambda f: -f["corr_6m"])[:12]


def suggested_weights(rows: list) -> None:
    """Inverse-volatility weights, capped, written onto each row in place."""
    inv = {}
    for r in rows:
        vol = None
        # ann_vol travels via technical context if present
        vol = r.get("_ann_vol_pct")
        if vol and vol > 5:
            inv[r["symbol"]] = 1.0 / vol
    if not inv:
        return
    total = sum(inv.values())
    cap = PORTFOLIO["max_position_pct"]
    for r in rows:
        w = inv.get(r["symbol"])
        r["suggested_weight_pct"] = (round(min(cap, w / total * 100), 1)
                                     if w else None)
