"""Elite Stock Screener - central configuration.

All scoring constants live here so methodology changes never require
touching pipeline code. Mirrors the Elite Stock Analysis Engine v2.0
standing rules exactly.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DOCS_DATA = ROOT / "docs" / "data"
HISTORY = DOCS_DATA / "history"

# ---------------------------------------------------------------- universes
NSE_INDEX_CSVS = {
    "midcap150": "https://nsearchives.nseindia.com/content/indices/ind_niftymidcap150list.csv",
    "smallcap250": "https://nsearchives.nseindia.com/content/indices/ind_niftysmallcap250list.csv",
}
UNIVERSE_CACHE = DATA / "universe.json"
UNIVERSE_MAX_AGE_DAYS = 30          # re-download index constituents monthly

# ---------------------------------------------------------------- framework weights (must sum to 1.0)
FRAMEWORK_WEIGHTS = {
    "buffett":       0.20,   # quality moat, ROE/ROCE, low debt, FCF
    "jhunjhunwala":  0.15,   # growth at reasonable price, earnings momentum
    "pabrai":        0.13,   # margin of safety, low downside
    "kacholia":      0.12,   # emerging small/midcap growth, operating leverage
    "kedia":         0.12,   # SMILE: small size, promoter skin-in-game, growth runway
    "blackrock":     0.10,   # institutional quality: liquidity, stability, governance
    "vanguard":      0.10,   # steady compounding, low volatility, consistency
    "damani":        0.08,   # cash-generative quality at reasonable valuation
}
assert abs(sum(FRAMEWORK_WEIGHTS.values()) - 1.0) < 1e-9

# ---------------------------------------------------------------- grade bands
GRADE_BANDS = [
    (9.0, "A+"), (8.5, "A"), (8.0, "A-"),
    (7.5, "B+"), (7.0, "B"), (6.5, "B-"),
    (5.5, "C"),  (4.5, "D"), (0.0, "F"),
]
VETO_GRADE_CAP = "C"          # any triggered hard veto caps the grade at C
VETO_SCORE_CAP = 6.4          # displayed composite is capped alongside the grade

def grade_for(score: float) -> str:
    for floor, g in GRADE_BANDS:
        if score >= floor:
            return g
    return "F"

# ---------------------------------------------------------------- hard vetoes
# Automatable from data:
V3_DE_LIMIT = 3.0             # V3: D/E > 3 AND declining CFO
# Not automatable from free data (surfaced as manual-verification checklist,
# never silently passed):
MANUAL_VETOES = {
    "V1": "SEBI / ED / exchange action - verify on SEBI + NSE surveillance lists",
    "V2": "Promoter pledge > 50% - verify NSE shareholding pattern",
    "V4": "Qualified audit opinion - verify latest annual report",
    "V5": "Promoter stake drop > 10% in 12 months - verify shareholding history",
}

# ---------------------------------------------------------------- hard filters (pre-scoring)
LIQUIDITY_FLOOR_CR = 3.0      # 20-day average turnover, Rs crore
REQUIRE_ABOVE_200DMA = False  # screener shows full graded universe; 200DMA is
                              # a displayed flag, not an exclusion (unlike the
                              # Smart Money Ledger, which trades momentum)
MIN_QUARTERS = 5              # quarters of financials needed to score at all
MIN_COVERAGE = 0.60           # min share of framework weight actually computed;
                              # below this the composite is statistically hollow
                              # and the stock is excluded (and counted)

# ---------------------------------------------------------------- Multibagger Radar rules
MULTIBAGGER = {
    "max_mcap_cr": 25_000,        # small enough to multiply
    "min_rev_cagr3y_pct": 15.0,   # sustained topline growth
    "min_pat_cagr3y_pct": 18.0,   # profit compounding
    "max_de": 0.6,                # low leverage
    "min_roe_pct": 15.0,          # capital efficiency
    "min_promoter_pct": 40.0,     # skin in the game (skipped if data missing,
                                  # flagged - never guessed)
    "require_above_200dma": True, # radar wants live uptrends only
    "max_names": 25,
}

# ---------------------------------------------------------------- output
TOP_N_PER_TAB = 30
YF_SUFFIX = ".NS"
