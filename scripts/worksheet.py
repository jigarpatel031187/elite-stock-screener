"""Auto-drafted ACE deep-dive worksheet (Deep-Dive Queue automation, part 1).

WHAT THIS AUTOMATES: mechanical prep for a deep-dive, never the judgment
itself. For every Lane 1 (and top Lane 2) candidate, this builds an 11-section
worksheet mirroring the Elite Stock Analysis Engine v2.0 methodology, with
each section marked AUTO (computed from data already in hand) or MANUAL
(requires your read, or a live web search this pipeline cannot perform).

WHY THE 6 MANDATORY WEB SEARCHES ARE NOT AUTO-FILLED: GitHub Actions has no
search API credentials, and fabricating "search results" would be worse than
leaving them blank - a false finding is more dangerous than an honest gap.
Instead, this generates the EXACT query strings your methodology calls for,
ready to paste, so the manual step is 6 searches instead of 6 searches you
also had to think up from scratch.

WHY QUALITATIVE SECTIONS STAY BLANK: moat narrative, management candor,
scuttlebutt, RPT-pattern judgment - these are exactly the layer that makes
ACE worth ~1.5 points more than the mechanical composite. Auto-filling them
with an LLM guess would make ACE a second mechanical layer wearing a
qualitative costume, and would break the divergence-drift alarm, whose entire
purpose is to detect when the mechanical picture has moved since a HUMAN
judgment was applied.
"""
from __future__ import annotations


def _search_queries(sym: str, name: str) -> list[str]:
    """The 6 mandatory searches from the ACE methodology, as ready queries."""
    return [
        f'"{name}" promoter pledge shareholding pattern 2026',
        f'"{name}" SEBI NSE BSE surveillance action order',
        f'"{name}" related party transactions annual report',
        f'"{name}" auditor resignation qualified opinion',
        f'"{name}" management commentary concall latest quarter',
        f'"{sym}" NSE stock news multibagger analysis 2026',
    ]


def sector_peers(row: dict, universe_rows: list[dict], n: int = 5) -> list[dict]:
    """Same-sector names from the already-scored universe, for comparison."""
    sec = row["fundamentals"].get("sector")
    if not sec:
        return []
    peers = [r for r in universe_rows
             if r["symbol"] != row["symbol"] and r["fundamentals"].get("sector") == sec]
    peers.sort(key=lambda r: -(r["composite"] or 0))
    return [{"symbol": p["symbol"], "grade": p["grade"], "composite": p["composite"],
             "pe": p["fundamentals"].get("pe"), "roe_pct": p["fundamentals"].get("roe_pct")}
            for p in peers[:n]]


def technical_structure(te: dict, cmp_: float | None) -> dict | None:
    """Illustrative support/resistance/ATR-based levels for Lane 1 only -
    volume-confirmed names, worth having a technical reference point ready.

    THIS IS NOT A RECOMMENDATION. It describes what the price structure
    already shows (recent swing low/high, ATR-based distance), the same way
    a chart would - it does not predict direction, does not size a position,
    and does not tell you to enter. Final entry/stop/target/sizing stays
    yours, same as it always has, consistent with your BHEL swing-setup
    convention where the LT call is separate from any swing overlay.
    """
    lo, hi, atr = te.get("swing_low_20"), te.get("swing_high_20"), te.get("atr14")
    if cmp_ is None or lo is None or hi is None:
        return None
    illustrative_stop = round(lo - atr, 2) if atr else round(lo * 0.98, 2)
    illustrative_target = hi
    rr = None
    if illustrative_stop and cmp_ > illustrative_stop:
        risk = cmp_ - illustrative_stop
        reward = illustrative_target - cmp_
        if risk > 0:
            rr = round(reward / risk, 2)
    return {
        "cmp": cmp_, "recent_swing_low_20d": lo, "recent_swing_high_20d": hi,
        "atr14": atr, "illustrative_stop_ref": illustrative_stop,
        "illustrative_target_ref": illustrative_target, "illustrative_rr": rr,
        "note": "Reference levels from price structure only (20-session "
                "swing low/high, ATR). NOT a recommendation - no position "
                "size, no entry timing, no directional call. Your own "
                "trade plan (entry zone, actual stop, target, theme tag) "
                "still goes in the fields below.",
    }


def draft_worksheet(row: dict, universe_rows: list[dict], lane: str = "lane2") -> dict:
    """Build the 11-section worksheet. Every field is either a real computed
    value or an explicit MANUAL placeholder - never a filled-in guess.
    `lane` = "lane1" attaches illustrative technical structure to section 10;
    lane2 stays fully manual (not yet volume-confirmed, lower actionability).
    """
    fu, te = row["fundamentals"], row["technical"]
    sym, name = row["symbol"], row["name"]

    return {
        "generated_for": sym, "name": name,
        "mode": "AUTO-DRAFTED PREP - not a decision. Fill MANUAL fields, then "
                "set your own ace_score/grade/verdict in leaderboard.json.",
        "sections": [
            {"n": 1, "title": "Company overview", "status": "AUTO", "data": {
                "sector": fu.get("sector"), "index": row["index"],
                "mcap_cr": fu.get("mcap_cr"), "latest_quarter": fu.get("latest_quarter")}},
            {"n": 2, "title": "Financial snapshot", "status": "AUTO", "data": {
                "pe": fu.get("pe"), "roe_pct": fu.get("roe_pct"),
                "op_margin_pct": fu.get("op_margin_pct"), "de": fu.get("de"),
                "rev_cagr3y_pct": fu.get("rev_cagr3y_pct"),
                "pat_cagr3y_pct": fu.get("pat_cagr3y_pct")}},
            {"n": 3, "title": "8-framework ACE scorecard", "status": "AUTO", "data": {
                "composite": row["composite"], "grade": row["grade"],
                "coverage": row["coverage"], "coverage_penalty": row.get("coverage_penalty"),
                "frameworks": row["frameworks"], "notes": row["framework_notes"]}},
            {"n": 4, "title": "Veto checklist", "status": "PARTIAL-AUTO", "data": {
                "V3_automated": row["veto_details"],
                "triggered": row["vetoes_triggered"],
                "manual_verify": row["manual_veto_checklist"],
                "instruction": "V1/V2/V4-V7 need your read of NSE/BSE/SEBI filings - "
                               "use the search queries in section 8."}},
            {"n": 5, "title": "Technical structure", "status": "AUTO", "data": {
                "cmp": row["cmp"], "above_200dma": te.get("above_200dma"),
                "dma200_dist_pct": te.get("dma200_dist_pct"),
                "dd_from_52wk": te.get("dd_from_52wk"), "ret_6m_pct": te.get("ret_6m_pct"),
                "rvol20": te.get("rvol20"), "turnover_cr": te.get("turnover_cr"),
                "volume_integrity_flags": row["integrity"]["flags"]}},
            {"n": 6, "title": "Sector peer comparison", "status": "AUTO",
             "data": {"peers": sector_peers(row, universe_rows)}},
            {"n": 7, "title": "Auto-generated bear case", "status": "AUTO",
             "data": {"bullets": row.get("bear_case", [])}},
            {"n": 8, "title": "Mandatory web searches (6)", "status": "MANUAL", "data": {
                "queries": _search_queries(sym, name),
                "instruction": "Run these 6 searches; log findings before scoring "
                               "moat/management/governance below."}},
            {"n": 9, "title": "Qualitative judgment", "status": "MANUAL", "data": {
                "moat_narrative": None, "management_candor": None,
                "scuttlebutt_notes": None, "governance_read": None,
                "ai_draft": None,
                "instruction": "This is the layer ACE exists for - the fields "
                               "above stay None until YOU fill them. If an "
                               "ai_draft is present below, it's an unreviewed "
                               "starting point from web search, not a "
                               "conclusion - confirm, edit, or reject it."}},
            {"n": 10, "title": "Trade plan / scenario",
             "status": "PARTIAL-AUTO" if lane == "lane1" else "MANUAL", "data": {
                "technical_reference": (technical_structure(te, row.get("cmp"))
                                        if lane == "lane1" else None),
                "entry_zone": None, "stop": None, "target": None, "rr": None,
                "theme_tag": None,
                "instruction": ("Reference levels above are illustrative only "
                                "(see note) - set your actual entry/stop/target/"
                                "sizing here." if lane == "lane1" else
                                "Fill this in during your deep-dive.")}},
            {"n": 11, "title": "Verdict", "status": "MANUAL", "data": {
                "ace_score": None, "grade": None, "verdict": None,
                "instruction": "Once decided, add/update this stock in "
                               "data/leaderboard.json (or ask Claude to)."}},
        ],
    }
