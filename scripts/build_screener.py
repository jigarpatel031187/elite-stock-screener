"""Elite Stock Screener - pipeline orchestrator.

Flow:
  1. Universe (Midcap 150 + Smallcap 250 from NSE, monthly-cached)
  2. Fundamentals (yfinance statements; weekly refresh, daily from cache)
  3. Price history (daily, full universe, batched)
  4. Per-stock: technical context -> hard filters -> 8 framework scores
     -> veto checks -> grade (veto caps at C)
  5. Outputs: midcap150 / smallcap250 tabs (top N by composite) +
     Multibagger Radar (rule-screened subset with its own gates)
  6. Score history appended for future out-of-sample grade-vs-return audits

Usage:
  python scripts/build_screener.py            # daily: cached fundamentals
  python scripts/build_screener.py --weekly   # full fundamentals refresh
"""
from __future__ import annotations
import argparse, json, sys
from datetime import datetime, timezone, timedelta

from config import (DATA, DOCS_DATA, HISTORY, FRAMEWORK_WEIGHTS, MULTIBAGGER,
                    LIQUIDITY_FLOOR_CR, TOP_N_PER_TAB,
                    VETO_SCORE_CAP, MIN_COVERAGE, grade_for)
from universe import get_universe
from datafeed import load_fundamentals, has_min_data, fetch_price_history
from technical import tech_context
from frameworks import score_all, _cagr, _latest
from vetoes import check_vetoes

IST = timezone(timedelta(hours=5, minutes=30))
SCORE_LOG = DATA / "score_history.json"

DISCLAIMER = ("Educational purposes only. Not investment advice. Not SEBI-registered "
              "research. Framework scores are quantified proxies of published investor "
              "philosophies computed from reported data; qualitative judgment is not "
              "automated. Verify independently before acting.")


def build_row(sym, meta, f, tech, scored):
    veto = check_vetoes(f)
    composite = scored["composite"]
    vetoed = bool(veto["triggered"])
    if vetoed and composite is not None:
        composite = min(composite, VETO_SCORE_CAP)
    grade = grade_for(composite) if composite is not None else "-"
    # grade cap is enforced via the composite score cap above (VETO_SCORE_CAP
    # sits below the B- floor, so any vetoed stock lands at C or lower)
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
        },
    }


def multibagger_pass(row) -> tuple[bool, list[str]]:
    """Apply radar gates; return (passes, reasons_failed)."""
    m, fails = MULTIBAGGER, []
    fu, te = row["fundamentals"], row["technical"]
    if row["vetoes_triggered"]:
        fails.append("hard veto")
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
        fails.append("promoter holding")     # missing data = flagged, not failed
    if m["require_above_200dma"] and not te["above_200dma"]:
        fails.append("below 200DMA")
    return (not fails), fails


def write_tab(tab: str, rows: list, meta: dict):
    payload = {**meta, "tab": tab, "count": len(rows), "stocks": rows}
    (DOCS_DATA / f"{tab}_latest.json").write_text(json.dumps(payload, indent=0))
    hdir = HISTORY / tab
    hdir.mkdir(parents=True, exist_ok=True)
    (hdir / f"{meta['trade_date']}.json").write_text(json.dumps(payload, indent=0))


def append_score_log(all_rows: list, trade_date: str):
    """Minimal per-run log for future grade-vs-forward-return audits."""
    log = json.loads(SCORE_LOG.read_text()) if SCORE_LOG.exists() else {}
    log[trade_date] = {r["symbol"]: {"c": r["composite"], "g": r["grade"],
                                     "p": r["cmp"]} for r in all_rows}
    # keep last 400 sessions
    for k in sorted(log)[:-400]:
        del log[k]
    SCORE_LOG.write_text(json.dumps(log, indent=0))


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

    rows, excl_fund, excl_liquid, excl_coverage = [], 0, 0, 0
    for sym in syms:
        f = funds.get(sym)
        if not has_min_data(f):
            excl_fund += 1
            continue
        tech = tech_context(hists.get(sym))
        if tech["turnover_cr"] is not None and tech["turnover_cr"] < LIQUIDITY_FLOOR_CR:
            excl_liquid += 1
            continue
        scored = score_all(f, tech)
        if scored["composite"] is None:
            excl_fund += 1
            continue
        if scored["coverage"] < MIN_COVERAGE:
            excl_coverage += 1
            continue
        rows.append(build_row(sym, universe[sym], f, tech, scored))

    rows.sort(key=lambda r: r["composite"], reverse=True)
    trade_date = now.strftime("%Y-%m-%d")
    meta = {
        "generated_at": now.isoformat(), "trade_date": trade_date,
        "universe_size": len(syms), "scored": len(rows),
        "excluded_no_fundamentals": excl_fund, "excluded_illiquid": excl_liquid,
        "excluded_low_coverage": excl_coverage, "min_coverage": MIN_COVERAGE,
        "universe_source": uni_src, "fundamentals_source": fund_src,
        "framework_weights": FRAMEWORK_WEIGHTS,
        "grade_note": "Any triggered hard veto caps grade at C. V1/V2/V4/V5 "
                      "require manual verification (checklist on each card).",
        "disclaimer": DISCLAIMER,
    }

    for tab in ("midcap150", "smallcap250"):
        tab_rows = [r for r in rows if r["index"] == tab][:TOP_N_PER_TAB]
        write_tab(tab, tab_rows, meta)
        print(f"[main] {tab}: {len(tab_rows)} cards, "
              f"leader {tab_rows[0]['symbol'] if tab_rows else '-'}")

    # Multibagger Radar: own gates, own ranking, near-miss transparency
    radar, near = [], []
    for r in rows:
        ok, fails = multibagger_pass(r)
        if ok:
            radar.append(r)
        elif len(fails) == 1 and "veto" not in fails[0]:
            near.append({**r, "near_miss": fails[0]})
    radar = radar[:MULTIBAGGER["max_names"]]
    write_tab("multibagger", radar,
              {**meta, "rules": MULTIBAGGER, "near_misses": near[:10]})
    print(f"[main] multibagger radar: {len(radar)} qualify, {len(near)} near-misses")

    append_score_log(rows, trade_date)
    return 0


if __name__ == "__main__":
    sys.exit(main())
