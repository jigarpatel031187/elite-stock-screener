"""Data acquisition: fundamentals (weekly cache) + price history (daily).

Honesty rules carried over from the Smart Money Ledger:
- Stocks with insufficient reported data are EXCLUDED and counted, never guessed.
- Every derived number is traceable to a yfinance statement line or NSE file.
"""
from __future__ import annotations
import json, math, time
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from config import DATA, YF_SUFFIX, MIN_QUARTERS

FUND_CACHE = DATA / "fundamentals.json"


def _f(x):
    """Coerce to float or None (never NaN into JSON)."""
    try:
        v = float(x)
        return None if math.isnan(v) or math.isinf(v) else v
    except (TypeError, ValueError):
        return None


def _row(df: pd.DataFrame, names: list[str]):
    """First matching row from a yfinance statement, as list ordered newest-first."""
    if df is None or df.empty:
        return None
    for n in names:
        if n in df.index:
            return [_f(v) for v in df.loc[n].tolist()]
    return None


def _extract_one(sym: str) -> dict | None:
    t = yf.Ticker(sym + YF_SUFFIX)
    try:
        qf = t.quarterly_financials
        af = t.financials
        bs = t.balance_sheet
        cf = t.cashflow
        info = t.info or {}
    except Exception as e:
        print(f"[fund] {sym}: fetch error {e}")
        return None

    q_rev = _row(qf, ["Total Revenue", "Operating Revenue"])
    q_pat = _row(qf, ["Net Income", "Net Income Common Stockholders"])
    q_dates = [str(c.date()) for c in qf.columns] if qf is not None and not qf.empty else []

    a_dates = [str(c.date()) for c in af.columns] if af is not None and not af.empty else []
    a_rev = _row(af, ["Total Revenue", "Operating Revenue"])
    a_pat = _row(af, ["Net Income", "Net Income Common Stockholders"])
    a_op  = _row(af, ["Operating Income", "EBIT"])

    total_debt = _row(bs, ["Total Debt"])
    equity = _row(bs, ["Stockholders Equity", "Total Equity Gross Minority Interest"])
    cash = _row(bs, ["Cash And Cash Equivalents",
                     "Cash Cash Equivalents And Short Term Investments"])
    cfo = _row(cf, ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"])
    capex = _row(cf, ["Capital Expenditure"])

    return {
        "asof": datetime.now(timezone.utc).isoformat(),
        "q_dates": q_dates, "q_rev": q_rev, "q_pat": q_pat, "a_dates": a_dates,
        "a_rev": a_rev, "a_pat": a_pat, "a_op": a_op,
        "total_debt": total_debt, "equity": equity, "cash": cash,
        "cfo": cfo, "capex": capex,
        "mcap": _f(info.get("marketCap")),
        "pe": _f(info.get("trailingPE")),
        "fwd_pe": _f(info.get("forwardPE")),
        "roe": _f(info.get("returnOnEquity")),
        "op_margin": _f(info.get("operatingMargins")),
        "profit_margin": _f(info.get("profitMargins")),
        "div_yield": _f(info.get("dividendYield")),
        "beta": _f(info.get("beta")),
        "promoter_pct": _f(info.get("heldPercentInsiders")),
        "sector": info.get("sector"), "industry": info.get("industry"),
    }


def refresh_fundamentals(symbols: list[str]) -> dict:
    """Full weekly refresh. Slow (~1 call/stock); only run with --weekly."""
    out, fails = {}, []
    for i, sym in enumerate(symbols):
        got = _extract_one(sym)
        if got:
            out[sym] = got
        else:
            fails.append(sym)
        if (i + 1) % 25 == 0:
            print(f"[fund] {i+1}/{len(symbols)} done, {len(fails)} failed")
        time.sleep(0.4)                      # be polite to Yahoo
    FUND_CACHE.write_text(json.dumps(out, indent=0))
    print(f"[fund] refresh complete: {len(out)} ok, {len(fails)} failed")
    return out


def load_fundamentals(symbols: list[str], weekly: bool) -> tuple[dict, str]:
    if weekly or not FUND_CACHE.exists():
        return refresh_fundamentals(symbols), "yfinance statements (fresh)"
    cached = json.loads(FUND_CACHE.read_text())
    missing = [s for s in symbols if s not in cached]
    if missing:                               # top up new index entrants only
        print(f"[fund] topping up {len(missing)} new symbols")
        for sym in missing:
            got = _extract_one(sym)
            if got:
                cached[sym] = got
            time.sleep(0.4)
        FUND_CACHE.write_text(json.dumps(cached, indent=0))
    ages = [c.get("asof", "")[:10] for c in cached.values() if c.get("asof")]
    return cached, f"yfinance statements (cached, oldest {min(ages) if ages else '?'})"


def market_regime() -> dict:
    """Fix #10: orthogonal regime inputs - Nifty trend + India VIX.
    Best-effort and non-fatal: a missing index feed must never block the run."""
    out = {"nifty_above_200dma": None, "nifty_3m_pct": None, "india_vix": None}
    try:
        df = yf.download(["^NSEI", "^INDIAVIX"], period="1y", interval="1d",
                         group_by="ticker", auto_adjust=True, progress=False)
        n = df["^NSEI"]["Close"].dropna()
        if len(n) >= 200:
            out["nifty_above_200dma"] = bool(n.iloc[-1] > n.rolling(200).mean().iloc[-1])
        if len(n) >= 63:
            out["nifty_3m_pct"] = round(float(n.iloc[-1] / n.iloc[-63] - 1) * 100, 1)
        v = df["^INDIAVIX"]["Close"].dropna()
        if len(v):
            out["india_vix"] = round(float(v.iloc[-1]), 1)
    except Exception as e:
        out["note"] = f"index feed unavailable: {e}"
    return out


def has_min_data(f: dict | None) -> bool:
    return bool(f and f.get("q_rev") and f.get("q_pat")
                and len([x for x in f["q_rev"] if x is not None]) >= MIN_QUARTERS - 1)


def fetch_price_history(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Batched 1y daily OHLCV, SPLIT/BONUS-ADJUSTED (auto_adjust=True).

    Raw prices poison every derived number the day a stock splits - the 200DMA
    flag flips, drawdowns hallucinate, attribution records crashes that never
    happened. Adjusted series make all history-derived math split-safe; the
    latest bar equals the actual traded price, so displayed CMP is unaffected.
    Callers may include ex-universe "ghost" symbols so attribution can follow
    stocks that left the index (survivorship fix)."""
    out: dict[str, pd.DataFrame] = {}
    CHUNK = 50
    for i in range(0, len(symbols), CHUNK):
        batch = symbols[i:i + CHUNK]
        tickers = [s + YF_SUFFIX for s in batch]
        try:
            df = yf.download(tickers, period="1y", interval="1d",
                             group_by="ticker", auto_adjust=True,
                             progress=False, threads=True)
        except Exception as e:
            print(f"[hist] batch {i//CHUNK} failed: {e}")
            continue
        for sym in batch:
            try:
                h = df[sym + YF_SUFFIX].dropna(how="all")
                if len(h) >= 60:
                    out[sym] = h
            except Exception:
                pass
        time.sleep(1.0)
    print(f"[hist] price history for {len(out)}/{len(symbols)} symbols")
    return out
