"""Technical context per stock - inputs to frameworks + display flags.

Not a trading system: the screener grades quality; technicals here serve as
context (trend state, liquidity, volatility) and Multibagger Radar gating.
"""
from __future__ import annotations
import math
import pandas as pd


def _f(x):
    try:
        v = float(x)
        return None if math.isnan(v) or math.isinf(v) else v
    except (TypeError, ValueError):
        return None


def tech_context(hist: pd.DataFrame | None) -> dict:
    out = {
        "cmp": None, "chg_pct": None, "above_200dma": False, "above_50dma": False,
        "dd_from_52wk": None, "ann_vol_pct": None, "turnover_cr": None,
        "ret_6m_pct": None, "dma200_dist_pct": None,
    }
    if hist is None or len(hist) < 60:
        return out
    close = hist["Close"].dropna()
    if close.empty:
        return out

    cmp_ = _f(close.iloc[-1])
    prev = _f(close.iloc[-2]) if len(close) >= 2 else None
    out["cmp"] = round(cmp_, 2) if cmp_ else None
    if cmp_ and prev:
        out["chg_pct"] = round((cmp_ / prev - 1) * 100, 2)

    if len(close) >= 200:
        dma200 = _f(close.rolling(200).mean().iloc[-1])
        if dma200 and cmp_:
            out["above_200dma"] = cmp_ > dma200
            out["dma200_dist_pct"] = round((cmp_ / dma200 - 1) * 100, 1)
    if len(close) >= 50:
        dma50 = _f(close.rolling(50).mean().iloc[-1])
        if dma50 and cmp_:
            out["above_50dma"] = cmp_ > dma50

    hi52 = _f(close.max())
    if hi52 and cmp_:
        out["dd_from_52wk"] = round((cmp_ / hi52 - 1) * 100, 1)   # negative %

    rets = close.pct_change().dropna()
    if len(rets) >= 60:
        out["ann_vol_pct"] = round(float(rets.std()) * math.sqrt(252) * 100, 1)

    if len(close) >= 126:
        base = _f(close.iloc[-126])
        if base and cmp_:
            out["ret_6m_pct"] = round((cmp_ / base - 1) * 100, 1)

    if "Volume" in hist.columns and cmp_:
        tail = hist.tail(20)
        turn = (tail["Close"] * tail["Volume"]).mean()
        t = _f(turn)
        out["turnover_cr"] = round(t / 1e7, 2) if t else None
    return out
