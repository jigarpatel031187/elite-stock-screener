"""Hard veto engine (Elite Engine v2.0, five vetoes).

Automation honesty: only V3 (D/E > 3 with declining CFO) is computable from
free structured data. V1 (SEBI/ED action), V2 (pledge > 50%), V4 (qualified
audit) and V5 (promoter stake drop) require sources that are not reliably
machine-readable for free - so they are surfaced as a per-stock MANUAL
VERIFICATION checklist on every card, never silently marked as passed.

Any triggered veto caps the composite at VETO_SCORE_CAP / grade C.
"""
from __future__ import annotations
from config import V3_DE_LIMIT, MANUAL_VETOES


def _latest(vals):
    if not vals:
        return None
    for v in vals:
        if v is not None:
            return v
    return None


def check_vetoes(f: dict) -> dict:
    """Returns {triggered: [..], details: {..}, manual_checklist: {..}}."""
    triggered, details = [], {}

    # ---- V3: D/E > 3 AND declining operating cash flow (fully automatable)
    debt, eq = _latest(f.get("total_debt")), _latest(f.get("equity"))
    cfo = [v for v in (f.get("cfo") or []) if v is not None]
    de = (debt / eq) if (debt is not None and eq not in (None, 0)) else None
    cfo_declining = len(cfo) >= 2 and cfo[0] < cfo[1]   # newest-first ordering
    if de is not None:
        details["de_ratio"] = round(de, 2)
    details["cfo_trend"] = ("declining" if cfo_declining else
                            "stable/rising" if len(cfo) >= 2 else "insufficient data")
    if de is not None and de > V3_DE_LIMIT and cfo_declining:
        triggered.append("V3")
        details["V3"] = f"D/E {de:.1f} > {V3_DE_LIMIT} with declining CFO"

    # ---- Screen-level red flags that are not vetoes but strengthen the
    #      manual checklist (e.g. negative equity makes V3 unmeasurable)
    if eq is not None and eq < 0:
        details["warning"] = "Negative shareholders' equity - D/E not meaningful, verify manually"

    return {
        "triggered": triggered,
        "details": details,
        "manual_checklist": dict(MANUAL_VETOES),
    }
