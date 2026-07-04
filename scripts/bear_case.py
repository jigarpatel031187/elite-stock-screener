"""Structural devil's-advocate (critique #8).

Eight frameworks scoring the same favorable framing can share the same blind
spot. This module's only job is the short case: for every card it extracts
the most damaging TRUE statements available in the data. It is deterministic
- no judgment, no mercy, no narrative - so skepticism is a system property,
not a habit the analyst must remember.
"""
from __future__ import annotations

from config import FRAMEWORK_WEIGHTS


def bear_case(row: dict, universe_pe_median: float | None) -> list[str]:
    bullets: list[str] = []
    fu, te, fw = row["fundamentals"], row["technical"], row["frameworks"]

    # weakest computed framework
    computed = {k: v for k, v in fw.items() if v is not None}
    if computed:
        worst = min(computed, key=computed.get)
        if computed[worst] < 5.0:
            bullets.append(f"Weakest lens: {worst.title()} scores just "
                           f"{computed[worst]:.1f}/10 ({row['framework_notes'][worst]})")

    # valuation vs live universe
    if fu.get("pe") and universe_pe_median:
        rel = fu["pe"] / universe_pe_median
        if rel > 1.5:
            bullets.append(f"PE {fu['pe']} is {rel:.1f}x the current universe median "
                           f"({universe_pe_median:.0f}) - priced for perfection in "
                           "this regime")

    # extension / chase risk
    d200 = te.get("dma200_dist_pct")
    if d200 is not None and d200 > 40:
        bullets.append(f"{d200:.0f}% above its 200 DMA - buying here is paying for "
                       "a move that already happened")
    ret6 = te.get("ret_6m_pct")
    if ret6 is not None and ret6 > 100:
        bullets.append(f"Already up {ret6:.0f}% in 6 months - entry now is momentum "
                       "confirmation, not discovery")

    # deceleration / cash quality
    vd = row.get("veto_details", {})
    if vd.get("cfo_trend") == "declining":
        bullets.append("Operating cash flow is DECLINING year-over-year while the "
                       "score rests on reported profits")

    # growth deceleration: latest-quarter growth below 3y trend
    # (proxied via fields already on the card)
    if (fu.get("pat_cagr3y_pct") is not None and fu.get("rev_cagr3y_pct") is not None
            and fu["pat_cagr3y_pct"] < fu["rev_cagr3y_pct"] - 5):
        bullets.append("PAT compounding slower than revenue - margins are being "
                       "given up to buy growth")

    # data honesty
    if row["coverage"] < 0.85:
        missing = [k for k, v in fw.items() if v is None]
        bullets.append(f"Only {row['coverage']:.0%} framework coverage - "
                       f"{', '.join(missing)} could not be scored; the composite "
                       "flatters what the data cannot see")
    if fu.get("promoter_pct") is None:
        bullets.append("Promoter holding unavailable - skin-in-the-game unverified")

    # volume integrity
    for f in row.get("integrity", {}).get("flags", []):
        bullets.append(f"Volume integrity: {f}")

    if not bullets:
        bullets.append("No structural negatives surfaced from reported data - "
                       "which itself deserves suspicion: the un-scorable risks "
                       "(V1/V2/V4/V5/V6/V7 checklist) are exactly where clean "
                       "screens die")
    return bullets[:5]
