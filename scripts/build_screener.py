"""Elite Stock Screener v1.1 - pipeline orchestrator.

v1.1 closes the nine institutional gaps from the PM critique:
  regime-relative valuation, volume-integrity gates, structural bear case,
  exit-review engine, portfolio concentration/correlation/sizing, and a
  self-attribution loop (grade-band forward returns + per-framework IC).

Usage:
  python scripts/build_screener.py            # daily: cached fundamentals
  python scripts/build_screener.py --weekly   # full fundamentals refresh
"""
from __future__ import annotations
import argparse, json, sys
from datetime import datetime, timezone, timedelta

from config import (DATA, DOCS_DATA, HISTORY, FRAMEWORK_WEIGHTS, MULTIBAGGER,
                    LIQUIDITY_FLOOR_CR, TOP_N_PER_TAB, VETO_SCORE_CAP,
                    MIN_COVERAGE, EXIT_RULES, grade_for, GRADE_BANDS)
from universe import get_universe
from datafeed import load_fundamentals, has_min_data, fetch_price_history
from technical import tech_context
from frameworks import score_all, _cagr, _latest
from vetoes import check_vetoes
from integrity import integrity_check
from attribution import log_run, compute_attribution
from portfolio import sector_concentration, correlation_flags, suggested_weights
from bear_case import bear_case

IST = timezone(timedelta(hours=5, minutes=30))
WATCH_STATE = DATA / "watch_state.json"
GRADE_ORDER = [g for _, g in GRADE_BANDS]

DISCLAIMER = ("Educational purposes only. Not investment advice. Not SEBI-registered "
              "research. Framework scores are quantified proxies of published investor "
              "philosophies computed from reported data; qualitative judgment is not "
              "automated. Suggested weights are a risk-budget illustration, not a "
              "recommendation. Verify independently before acting.")


# ---------------------------------------------------------------- row builder
def build_row(sym, meta, f, tech, scored, integ):
    veto = check_vetoes(f)
    composite = scored["composite"]
    vetoed = bool(veto["triggered"])
    if vetoed and composite is not None:
        composite = min(composite, VETO_SCORE_CAP)
    grade = grade_for(composite) if composite is not None else "-"
    rev_c3 = _cagr(f.get("a_rev"), 3)
    pat_c3 = _cagr(f.get("a_pat"), 3)
    debt, eq = _latest(f.get("total_debt")), _latest(f.get("equity"))
    de = round(debt / eq, 2) if (debt is not None and eq not in (None, 0) and eq > 0) else None
    return {
        "symbol": sym, "name": meta["name"], "index": meta["index"],
        "cmp": tech["cmp"], "chg_pct": tech["chg_pct"],
        "composite": composite, "grade": grade,
        "coverage": scored["coverage"],
        "frameworks": scored["frameworks"],
        "framework_notes": scored["notes"],
        "vetoes_triggered": veto["triggered"],
        "veto_details": veto["details"],
        "manual_veto_checklist": veto["manual_checklist"],
        "integrity": {"flags": integ["flags"], "sessions": integ["sessions"],
                      "top3_vol_share": integ["top3_vol_share"],
                      "radar_ok": integ["radar_ok"]},
        "fundamentals": {
            "mcap_cr": round(f["mcap"] / 1e7) if f.get("mcap") else None,
            "pe": round(f["pe"], 1) if f.get("pe") else None,
            "roe_pct": round(f["roe"] * 100, 1) if f.get("roe") is not None else None,
            "op_margin_pct": round(f["op_margin"] * 100, 1) if f.get("op_margin") is not None else None,
            "de": de,
            "rev_cagr3y_pct": round(rev_c3, 1) if rev_c3 is not None else None,
            "pat_cagr3y_pct": round(pat_c3, 1) if pat_c3 is not None else None,
            "promoter_pct": round(f["promoter_pct"] * 100, 1) if f.get("promoter_pct") is not None else None,
            "sector": f.get("sector"),
            "latest_quarter": f["q_dates"][0] if f.get("q_dates") else None,
        },
        "technical": {
            "above_200dma": tech["above_200dma"],
            "above_50dma": tech["above_50dma"],
            "dma200_dist_pct": tech["dma200_dist_pct"],
            "dd_from_52wk": tech["dd_from_52wk"],
            "ret_6m_pct": tech["ret_6m_pct"],
            "turnover_cr": tech["turnover_cr"],
            "ann_vol_pct": tech["ann_vol_pct"],
        },
        "_ann_vol_pct": tech["ann_vol_pct"],       # sizing input (stripped later)
    }


# ---------------------------------------------------------------- exit engine (#4)
def run_exit_engine(tab_members: dict[str, list], all_rows_by_sym: dict,
                    trade_date: str) -> dict:
    """Track entry scores; flag decayed names for EXIT REVIEW.

    Entry-oriented systems die of never selling. Every name that enters a tab's
    published list gets its entry composite recorded; from then on decay vs
    entry (score, grade bands, or a fresh veto) raises an explicit exit flag
    that persists until the name recovers or leaves the state after
    stale_sessions_drop sessions outside all tabs.
    """
    state = json.loads(WATCH_STATE.read_text()) if WATCH_STATE.exists() else {}
    current = {s for members in tab_members.values() for s in members}
    exit_review = []

    for sym in current:
        r = all_rows_by_sym[sym]
        if sym not in state:
            state[sym] = {"entered": trade_date, "entry_composite": r["composite"],
                          "entry_grade": r["grade"], "sessions_out": 0}
        state[sym]["sessions_out"] = 0

    for sym in list(state):
        st = state[sym]
        r = all_rows_by_sym.get(sym)
        if sym not in current:
            st["sessions_out"] += 1
            if st["sessions_out"] > EXIT_RULES["stale_sessions_drop"]:
                del state[sym]
                continue
        if r is None:
            continue
        reasons = []
        if (r["composite"] is not None and st["entry_composite"] is not None
                and st["entry_composite"] - r["composite"] >= EXIT_RULES["composite_decay"]):
            reasons.append(f"composite {st['entry_composite']} -> {r['composite']} "
                           f"since entry {st['entered']}")
        try:
            fell = (GRADE_ORDER.index(r["grade"])
                    - GRADE_ORDER.index(st["entry_grade"]))
            if fell >= EXIT_RULES["grade_floor_bands"]:
                reasons.append(f"grade {st['entry_grade']} -> {r['grade']} "
                               f"({fell} bands)")
        except ValueError:
            pass
        if r["vetoes_triggered"]:
            reasons.append(f"veto {','.join(r['vetoes_triggered'])} now active")
        if reasons:
            exit_review.append({"symbol": sym, "name": r["name"],
                                "entered": st["entered"],
                                "entry_composite": st["entry_composite"],
                                "current_composite": r["composite"],
                                "grade": r["grade"], "reasons": reasons})

    WATCH_STATE.write_text(json.dumps(state, indent=0))
    return {"count": len(exit_review), "names": exit_review,
            "rule": (f"review when composite decays >= {EXIT_RULES['composite_decay']} "
                     f"vs entry, grade falls {EXIT_RULES['grade_floor_bands']}+ bands, "
                     "or any veto fires")}


# ---------------------------------------------------------------- multibagger gates
def multibagger_pass(row) -> tuple[bool, list[str]]:
    m, fails = MULTIBAGGER, []
    fu, te = row["fundamentals"], row["technical"]
    if row["vetoes_triggered"]:
        fails.append("hard veto")
    if not row["integrity"]["radar_ok"]:
        fails.append("volume integrity")           # critique #5 hard gate
    if fu["mcap_cr"] is None or fu["mcap_cr"] > m["max_mcap_cr"]:
        fails.append(f"mcap > {m['max_mcap_cr']}cr" if fu["mcap_cr"] else "mcap unknown")
    if fu["rev_cagr3y_pct"] is None or fu["rev_cagr3y_pct"] < m["min_rev_cagr3y_pct"]:
        fails.append("rev CAGR")
    if fu["pat_cagr3y_pct"] is None or fu["pat_cagr3y_pct"] < m["min_pat_cagr3y_pct"]:
        fails.append("PAT CAGR")
    if fu["de"] is not None and fu["de"] > m["max_de"]:
        fails.append("leverage")
    if fu["roe_pct"] is None or fu["roe_pct"] < m["min_roe_pct"]:
        fails.append("ROE")
    if fu["promoter_pct"] is not None and fu["promoter_pct"] < m["min_promoter_pct"]:
        fails.append("promoter holding")
    if m["require_above_200dma"] and not te["above_200dma"]:
        fails.append("below 200DMA")
    return (not fails), fails


def write_tab(tab: str, rows: list, meta: dict):
    clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    payload = {**meta, "tab": tab, "count": len(clean), "stocks": clean}
    (DOCS_DATA / f"{tab}_latest.json").write_text(json.dumps(payload, indent=0))
    hdir = HISTORY / tab
    hdir.mkdir(parents=True, exist_ok=True)
    (hdir / f"{meta['trade_date']}.json").write_text(json.dumps(payload, indent=0))


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weekly", action="store_true", help="full fundamentals refresh")
    args = ap.parse_args()

    now = datetime.now(IST)
    universe, uni_src = get_universe()
    syms = sorted(universe)
    print(f"[main] universe {len(syms)} symbols ({uni_src})")

    funds, fund_src = load_fundamentals(syms, weekly=args.weekly)
    hists = fetch_price_history(syms)

    # ---- regime (#7): live universe median PE - valuation is relative, not absolute
    pes = sorted(f["pe"] for f in funds.values()
                 if f and f.get("pe") and 0 < f["pe"] < 300)
    regime = {"universe_pe_median": round(pes[len(pes) // 2], 1) if pes else None,
              "note": "valuation components in Pabrai/Damani are scored vs this "
                      "median; 'cheap' is regime-dependent"}
    print(f"[main] regime: universe median PE {regime['universe_pe_median']}")

    rows, excl_fund, excl_liquid, excl_coverage = [], 0, 0, 0
    for sym in syms:
        f = funds.get(sym)
        if not has_min_data(f):
            excl_fund += 1
            continue
        hist = hists.get(sym)
        tech = tech_context(hist)
        if tech["turnover_cr"] is not None and tech["turnover_cr"] < LIQUIDITY_FLOOR_CR:
            excl_liquid += 1
            continue
        scored = score_all(f, tech, regime)
        if scored["composite"] is None:
            excl_fund += 1
            continue
        if scored["coverage"] < MIN_COVERAGE:
            excl_coverage += 1
            continue
        rows.append(build_row(sym, universe[sym], f, tech, scored,
                              integrity_check(hist)))

    rows.sort(key=lambda r: (r["composite"] is not None, r["composite"]), reverse=True)
    by_sym = {r["symbol"]: r for r in rows}
    trade_date = now.strftime("%Y-%m-%d")

    # ---- attribution (#1/#2): judge the system before publishing today's scores
    current_prices = {r["symbol"]: r["cmp"] for r in rows if r["cmp"]}
    attribution = compute_attribution(current_prices)

    # ---- bear case (#8) on every published row
    for r in rows:
        r["bear_case"] = bear_case(r, regime["universe_pe_median"])

    meta = {
        "generated_at": now.isoformat(), "trade_date": trade_date,
        "universe_size": len(syms), "scored": len(rows),
        "excluded_no_fundamentals": excl_fund, "excluded_illiquid": excl_liquid,
        "excluded_low_coverage": excl_coverage, "min_coverage": MIN_COVERAGE,
        "universe_source": uni_src, "fundamentals_source": fund_src,
        "framework_weights": FRAMEWORK_WEIGHTS,
        "regime": regime,
        "attribution": attribution,
        "grade_note": "Any triggered hard veto caps grade at C. V1/V2/V4-V7 "
                      "require manual verification (checklist on each card).",
        "disclaimer": DISCLAIMER,
    }

    tab_members: dict[str, list] = {}
    for tab in ("midcap150", "smallcap250"):
        tab_rows = [r for r in rows if r["index"] == tab][:TOP_N_PER_TAB]
        suggested_weights(tab_rows)                      # (#9)
        conc = sector_concentration(tab_rows)            # (#3)
        corr = correlation_flags(tab_rows, hists)        # (#3)
        tab_members[tab] = [r["symbol"] for r in tab_rows]
        write_tab(tab, tab_rows, {**meta,
                  "portfolio": {"sector_concentration": conc,
                                "correlation_flags": corr,
                                "sizing_note": "inverse-volatility weights, "
                                               "capped at 8% - risk illustration only"}})
        print(f"[main] {tab}: {len(tab_rows)} cards, "
              f"leader {tab_rows[0]['symbol'] if tab_rows else '-'}, "
              f"{len(conc['warnings'])} concentration warnings")

    radar, near = [], []
    for r in rows:
        ok, fails = multibagger_pass(r)
        if ok:
            radar.append(r)
        elif len(fails) == 1 and fails[0] not in ("hard veto", "volume integrity"):
            near.append({"symbol": r["symbol"], "name": r["name"],
                         "composite": r["composite"], "near_miss": fails[0]})
    radar = radar[:MULTIBAGGER["max_names"]]
    suggested_weights(radar)
    tab_members["multibagger"] = [r["symbol"] for r in radar]
    write_tab("multibagger", radar, {**meta, "rules": MULTIBAGGER,
              "near_misses": near[:10],
              "portfolio": {"sector_concentration": sector_concentration(radar),
                            "correlation_flags": correlation_flags(radar, hists),
                            "sizing_note": "inverse-volatility, capped 8%"}})
    print(f"[main] multibagger radar: {len(radar)} qualify, {len(near)} near-misses")

    # ---- exit engine (#4) after tab membership is known
    exits = run_exit_engine(tab_members, by_sym, trade_date)
    print(f"[main] exit review: {exits['count']} names flagged")
    exit_payload = {**meta, "exit_review": exits}
    (DOCS_DATA / "system_latest.json").write_text(json.dumps(exit_payload, indent=0))

    # ---- log today for future attribution (#1)
    log_run(rows, trade_date)
    return 0


if __name__ == "__main__":
    sys.exit(main())
