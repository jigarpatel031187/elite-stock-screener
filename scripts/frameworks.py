"""The eight investor-framework scorers (Elite Engine v2.0 weights).

Each scorer returns (score 0-10, note) from reported data only. These are
quantified PROXIES of each investor's published philosophy - the qualitative
judgment layers (management quality, moat narrative, scuttlebutt) cannot be
automated and are deliberately out of scope for the automated screener; the
chat-based Elite Engine deep-dive remains the tool for that.

Conventions: fundamentals lists are newest-first (yfinance order).
All ratios guard against None/zero. A scorer that cannot compute returns
(None, reason) and its weight is redistributed pro-rata across computable
frameworks for that stock - missing data is never scored as zero or ten.
"""
from __future__ import annotations
import math

from config import FRAMEWORK_WEIGHTS


# ---------------------------------------------------------------- helpers
def _latest(vals):
    if not vals:
        return None
    for v in vals:
        if v is not None:
            return v
    return None


def _cagr(vals, years):
    """CAGR % from newest-first annual list, using up to `years` intervals."""
    vals = [v for v in (vals or []) if v is not None]
    if len(vals) < 2:
        return None
    n = min(years, len(vals) - 1)
    new, old = vals[0], vals[n]
    if old is None or new is None or old <= 0 or new <= 0:
        return None
    return (pow(new / old, 1 / n) - 1) * 100


def _yoy_quarter(f):
    """Latest quarter vs same quarter last year (4 back), rev + pat."""
    def yoy(series):
        s = f.get(series) or []
        if len(s) >= 5 and s[0] is not None and s[4] not in (None, 0):
            if s[4] > 0:
                return (s[0] / s[4] - 1) * 100
        return None
    return yoy("q_rev"), yoy("q_pat")


def _scale(x, lo, hi):
    """Linear map [lo, hi] -> [0, 10], clamped."""
    if x is None:
        return None
    return max(0.0, min(10.0, (x - lo) / (hi - lo) * 10))


def _mean(parts, min_parts=2):
    """Average of computed components. A framework built on fewer than
    `min_parts` real inputs returns None - one lonely data point must not
    masquerade as a philosophy score."""
    parts = [p for p in parts if p is not None]
    return sum(parts) / len(parts) if len(parts) >= min_parts else None


# ---------------------------------------------------------------- scorers
def buffett(f, tech):
    """Quality moat: high stable ROE, fat margins, low debt, real FCF."""
    roe = _scale((f.get("roe") or 0) * 100 if f.get("roe") is not None else None, 5, 25)
    margin = _scale((f.get("op_margin") or 0) * 100 if f.get("op_margin") is not None else None, 5, 25)
    debt, eq = _latest(f.get("total_debt")), _latest(f.get("equity"))
    de = (debt / eq) if (debt is not None and eq not in (None, 0) and eq > 0) else None
    debt_s = _scale(2.0 - de, 0, 2.0) if de is not None else None   # D/E 0 -> 10, 2 -> 0
    cfo, capex = _latest(f.get("cfo")), _latest(f.get("capex"))
    fcf = (cfo + capex) if (cfo is not None and capex is not None) else None  # capex negative
    fcf_s = 10.0 if (fcf is not None and fcf > 0) else (2.0 if fcf is not None else None)
    s = _mean([roe, margin, debt_s, fcf_s])
    return s, "ROE/margin/leverage/FCF quality"


def jhunjhunwala(f, tech):
    """Growth at a reasonable price with earnings momentum."""
    rev_y, pat_y = _yoy_quarter(f)
    growth = _scale(pat_y, 0, 40)
    pe = f.get("pe")
    peg = (pe / pat_y) if (pe and pat_y and pat_y > 0) else None
    peg_s = _scale(3.0 - peg, 0, 3.0) if peg is not None else None  # PEG 0 -> 10, 3 -> 0
    trend = 10.0 if tech.get("above_200dma") else 4.0
    s = _mean([growth, peg_s, trend])
    return s, "earnings momentum vs valuation"


def pabrai(f, tech):
    """Heads I win, tails I don't lose much: low leverage, cheapness, cash."""
    debt, eq, cash = _latest(f.get("total_debt")), _latest(f.get("equity")), _latest(f.get("cash"))
    de = (debt / eq) if (debt is not None and eq not in (None, 0) and eq > 0) else None
    debt_s = _scale(1.5 - de, 0, 1.5) if de is not None else None
    pe_s = _scale(50 - f["pe"], 10, 40) if f.get("pe") else None    # cheaper -> higher
    net_cash = 10.0 if (cash is not None and debt is not None and cash > debt) else None
    dd_s = _scale(40 + tech.get("dd_from_52wk", 0), 0, 40) if tech.get("dd_from_52wk") is not None else None
    s = _mean([debt_s, pe_s, net_cash, dd_s])
    return s, "margin-of-safety / downside protection"


def kacholia(f, tech):
    """Emerging growth with operating leverage kicking in."""
    rev_c = _cagr(f.get("a_rev"), 3)
    pat_c = _cagr(f.get("a_pat"), 3)
    rev_s = _scale(rev_c, 5, 30)
    pat_s = _scale(pat_c, 5, 40)
    op_lev = 10.0 if (rev_c is not None and pat_c is not None and pat_c > rev_c) else \
             (4.0 if (rev_c is not None and pat_c is not None) else None)
    mcap_cr = (f.get("mcap") or 0) / 1e7 if f.get("mcap") else None
    size_s = _scale(60_000 - mcap_cr, 0, 55_000) if mcap_cr else None  # smaller -> higher
    s = _mean([rev_s, pat_s, op_lev, size_s])
    return s, "3y growth + operating leverage + size runway"


def kedia(f, tech):
    """SMILE: small size, large aspiration, promoter skin-in-game."""
    mcap_cr = (f.get("mcap") or 0) / 1e7 if f.get("mcap") else None
    size_s = _scale(40_000 - mcap_cr, 0, 38_000) if mcap_cr else None
    promoter = f.get("promoter_pct")
    prom_s = _scale(promoter * 100 if promoter is not None else None, 20, 70)
    rev_y, _ = _yoy_quarter(f)
    grow_s = _scale(rev_y, 0, 30)
    s = _mean([size_s, prom_s, grow_s])
    note = "size + promoter holding + growth"
    if promoter is None:
        note += " (promoter % unavailable - verify)"
    return s, note


def blackrock(f, tech):
    """Institutional quality: investability, liquidity, stability."""
    turn_s = _scale(tech.get("turnover_cr"), 1, 25)
    mcap_cr = (f.get("mcap") or 0) / 1e7 if f.get("mcap") else None
    mcap_s = _scale(mcap_cr, 1_000, 30_000) if mcap_cr else None
    roe = f.get("roe")
    roe_s = _scale(roe * 100 if roe is not None else None, 8, 22)
    vol_s = _scale(2.0 - (f.get("beta") or 1.0), 0, 1.5) if f.get("beta") is not None else None
    s = _mean([turn_s, mcap_s, roe_s, vol_s])
    return s, "liquidity / scale / quality stability"


def vanguard(f, tech):
    """Boring compounding: consistency, low volatility, shareholder returns."""
    a_pat = [v for v in (f.get("a_pat") or []) if v is not None]
    consist = 10.0 if (len(a_pat) >= 3 and all(v > 0 for v in a_pat[:3])) else \
              (3.0 if a_pat else None)
    beta_s = _scale(1.6 - (f.get("beta") or 1.0), 0, 1.2) if f.get("beta") is not None else None
    div_s = _scale((f.get("div_yield") or 0) * 100, 0, 2.5) if f.get("div_yield") is not None else 3.0
    vol_s = _scale(50 - tech.get("ann_vol_pct", 50), 0, 35) if tech.get("ann_vol_pct") else None
    s = _mean([consist, beta_s, div_s, vol_s])
    return s, "profit consistency / low volatility"


def damani(f, tech):
    """Cash-generative quality at a sane price; earnings you can bank."""
    cfo, pat = _latest(f.get("cfo")), _latest(f.get("a_pat"))
    conv = (cfo / pat) if (cfo is not None and pat not in (None, 0) and pat > 0) else None
    conv_s = _scale(conv, 0.4, 1.2) if conv is not None else None
    pm = f.get("profit_margin")
    pm_s = _scale(pm * 100 if pm is not None else None, 3, 18)
    pe_s = _scale(60 - f["pe"], 15, 50) if f.get("pe") else None
    s = _mean([conv_s, pm_s, pe_s])
    return s, "CFO/PAT conversion + margin + valuation"


SCORERS = {
    "buffett": buffett, "jhunjhunwala": jhunjhunwala, "pabrai": pabrai,
    "kacholia": kacholia, "kedia": kedia, "blackrock": blackrock,
    "vanguard": vanguard, "damani": damani,
}


def score_all(f: dict, tech: dict) -> dict:
    """Run all 8 frameworks; redistribute weights of non-computable ones."""
    raw, notes = {}, {}
    for name, fn in SCORERS.items():
        try:
            s, note = fn(f, tech)
        except Exception as e:
            s, note = None, f"error: {e}"
        raw[name] = None if s is None else round(s, 2)
        notes[name] = note

    usable = {k: v for k, v in raw.items() if v is not None}
    if not usable:
        return {"composite": None, "frameworks": raw, "notes": notes, "coverage": 0}

    wsum = sum(FRAMEWORK_WEIGHTS[k] for k in usable)
    composite = sum(v * FRAMEWORK_WEIGHTS[k] for k, v in usable.items()) / wsum
    return {
        "composite": round(composite, 2),
        "frameworks": raw,
        "notes": notes,
        "coverage": round(wsum, 2),          # 1.0 = all 8 computed
    }
