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
                    MIN_COVERAGE, EXIT_RULES, QUEUE, LEADERBOARD_FILE,
                    LEADERBOARD_CUTOFF, LEADERBOARD_MAX,
                    LEADERBOARD_PROVISIONAL_TARGET, PRIMARY_INDICES,
                    INTEGRITY, V3_DE_LIMIT, MANUAL_VETOES, RUN_GUARD,
                    COVERAGE_PENALTY, RADAR_EXTENSION,
                    grade_for, GRADE_BANDS)
from universe import get_universe
from datafeed import load_fundamentals, has_min_data, fetch_price_history, market_regime
from technical import tech_context
from frameworks import score_all, _cagr, _latest
from vetoes import check_vetoes
from integrity import integrity_check
from attribution import log_run, compute_attribution, ghost_symbols
from portfolio import sector_concentration, correlation_flags, suggested_weights
from bear_case import bear_case
from worksheet import draft_worksheet
from digest import build_digest, save_state
from research import enrich_queue

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
    # fix #4: opacity is penalized, never rewarded. Weight redistribution keeps
    # the math sound, but a stock scored on 65% of the frameworks must not
    # outrank an equal stock scored on 100% - missing data hides risk.
    cov_penalty = round((1.0 - scored["coverage"]) * COVERAGE_PENALTY, 2)
    if composite is not None and cov_penalty > 0:
        composite = max(0.0, round(composite - cov_penalty, 2))
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
        "coverage_penalty": cov_penalty,
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
            "rvol20": tech["rvol20"],
            "swing_low_20": tech.get("swing_low_20"),
            "swing_high_20": tech.get("swing_high_20"),
            "atr14": tech.get("atr14"),
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
    # fix #5: promoter % (yfinance heldPercentInsiders) is NOT the SEBI promoter
    # category and is unreliable for NSE names - display-only until wired to the
    # NSE quarterly shareholding pattern. It no longer gates or scores anything.
    if m["require_above_200dma"] and not te["above_200dma"]:
        fails.append("below 200DMA")
    # fix #6: pre-run is enforced, not asserted - extension caps
    d200 = te.get("dma200_dist_pct")
    if d200 is not None and d200 > RADAR_EXTENSION["max_dma200_ext_pct"]:
        fails.append(f"over-extended ({d200:.0f}% above 200DMA)")
    r6 = te.get("ret_6m_pct")
    if r6 is not None and r6 > RADAR_EXTENSION["max_ret6m_pct"]:
        fails.append(f"already ran ({r6:.0f}% in 6m - momentum confirmation, not discovery)")
    if row["coverage"] < RADAR_EXTENSION["min_coverage"]:
        fails.append("framework coverage below radar bar")
    return (not fails), fails


def write_tab(tab: str, rows: list, meta: dict):
    clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    payload = {**meta, "tab": tab, "count": len(clean), "stocks": clean}
    (DOCS_DATA / f"{tab}_latest.json").write_text(json.dumps(payload, indent=0))
    hdir = HISTORY / tab
    hdir.mkdir(parents=True, exist_ok=True)
    (hdir / f"{meta['trade_date']}.json").write_text(json.dumps(payload, indent=0))


# ---------------------------------------------------------------- queue (v1.2)
def build_queue(rows: list, leaderboard_syms: set) -> dict:
    """Deep-Dive Queue: mechanically qualified names awaiting your ACE session.

    Lane 1 = volume-confirmed today (RVol >= threshold on an up day): act soon.
    Lane 2 = quality qualified, volume not confirming yet: watch.
    Names already on the Elite Leaderboard are excluded - the queue exists to
    feed the leaderboard, not to echo it.
    """
    lane1, lane2 = [], []
    for r in rows:
        if r["index"] not in PRIMARY_INDICES:
            continue
        if (r["composite"] is None or r["composite"] < QUEUE["min_composite"]
                or r["vetoes_triggered"] or r["symbol"] in leaderboard_syms):
            continue
        rvol = r["technical"].get("rvol20")
        up = (r["chg_pct"] or 0) > 0
        confirmed = (rvol is not None and rvol >= QUEUE["lane1_rvol"]
                     and (up or not QUEUE["lane1_requires_up_day"]))
        entry = {"symbol": r["symbol"], "name": r["name"], "index": r["index"],
                 "composite": r["composite"], "grade": r["grade"],
                 "cmp": r["cmp"], "chg_pct": r["chg_pct"], "rvol20": rvol,
                 "integrity_flags": r["integrity"]["flags"],
                 "bear_case": r["bear_case"][:3],
                 "sector": r["fundamentals"].get("sector")}
        (lane1 if confirmed else lane2).append(entry)
    lane1 = lane1[:QUEUE["max_names"]]
    lane2 = lane2[:QUEUE["max_names"]]
    return {"lane1_volume_confirmed": lane1, "lane2_watching": lane2,
            "rule": (f"composite >= {QUEUE['min_composite']}, no vetoes, not on "
                     f"leaderboard; Lane 1 = RVol >= {QUEUE['lane1_rvol']}x 20d avg "
                     "on an up day. A queue entry is an invitation to run the full "
                     "ACE deep-dive - never a buy signal by itself.")}


# ---------------------------------------------------------------- leaderboard (v1.2)
DIVERGENCE_STATE = DATA / "divergence_state.json"
DIVERGENCE_DRIFT_ALERT = 1.0


def _why_for(r: dict) -> dict:
    """Shared rationale builder for both confirmed and provisional entries."""
    fw = {k: v for k, v in r["frameworks"].items() if v is not None}
    top3 = sorted(fw.items(), key=lambda kv: -kv[1])[:3]
    corroborated = (r["composite"] or 0) >= 7.0 and not r["vetoes_triggered"]
    return {
        "top_frameworks": [{"name": k, "score": v} for k, v in top3],
        "mechanical_corroboration": ("Mechanical screen currently agrees "
            f"(composite {r['composite']}, {r['grade']}) - your deep-dive "
            "call and the automated read are aligned." if corroborated else
            "Mechanical screen currently does NOT corroborate as strongly "
            f"(composite {r['composite']}, {r['grade']}) - your ACE score "
            "rests more on the qualitative layer than on the current numbers."),
        "bear_case": r.get("bear_case", []),
        "vetoes_now": r["vetoes_triggered"],
    }


def build_confirmed_leaderboard(by_sym: dict) -> dict:
    """Elite Leaderboard: manual ACE results, machine-refreshed prices.

    ace_score / grade / verdict / theme come ONLY from data/leaderboard.json
    (your deep-dive output). The pipeline adds live cmp/chg, the current
    mechanical composite for divergence-watching, and any active veto or
    exit-review signal on board names. ACE never changes on a timer.
    """
    if not LEADERBOARD_FILE.exists():
        return {"entries": [], "note": "data/leaderboard.json not found"}
    lb = json.loads(LEADERBOARD_FILE.read_text())
    dstate = (json.loads(DIVERGENCE_STATE.read_text())
              if DIVERGENCE_STATE.exists() else {})
    entries = []
    for e in sorted(lb.get("entries", []), key=lambda x: -(x.get("ace_score") or 0)):
        r = by_sym.get(e["symbol"])
        div = (round(e["ace_score"] - r["composite"], 2)
               if r and r["composite"] is not None and e.get("ace_score") else None)
        # fix #7: ACE runs structurally ~1-1.5 richer than the mechanical proxy,
        # so the ABSOLUTE gap is noise. Baseline each name's gap when first seen
        # and alert only on DRIFT from that baseline - a widening gap means the
        # mechanical picture deteriorated since your deep-dive.
        drift = None
        if div is not None:
            if e["symbol"] not in dstate:
                dstate[e["symbol"]] = {"baseline_div": div,
                                       "set_on": datetime.now(IST).strftime("%Y-%m-%d")}
            drift = round(div - dstate[e["symbol"]]["baseline_div"], 2)
        why = {**_why_for(r), "verdict": e.get("verdict"), "theme": e.get("theme")} if r else None
        entries.append({**e,
            "provisional": False,
            "cmp": r["cmp"] if r else None,
            "chg_pct": r["chg_pct"] if r else None,
            "mech_composite": r["composite"] if r else None,
            "mech_grade": r["grade"] if r else None,
            "divergence": div,
            "divergence_baseline": dstate.get(e["symbol"], {}).get("baseline_div"),
            "divergence_drift": drift,
            "re_review": bool(drift is not None and drift >= DIVERGENCE_DRIFT_ALERT),
            "vetoes_now": r["vetoes_triggered"] if r else [],
            "in_universe": r is not None and r.get("index") != "board-only",
            "why": why,
        })
    dstate = {s: v for s, v in dstate.items()
              if s in {e["symbol"] for e in lb.get("entries", [])}}
    DIVERGENCE_STATE.write_text(json.dumps(dstate, indent=0))
    lb_syms_out = {e["symbol"] for e in entries}
    return {"entries": entries[:LEADERBOARD_MAX], "syms": lb_syms_out,
            "cutoff": LEADERBOARD_CUTOFF, "max": LEADERBOARD_MAX,
            "todo": lb.get("todo"),
            "note": ("ACE scores are manual deep-dive results; CMP refreshes "
                     "nightly. 'divergence' = ACE minus today's mechanical "
                     "composite - a widening gap is a re-review prompt, exactly "
                     "the 'nobody re-ran Cummins' failure the exit engine exists "
                     "to prevent.")}


def fill_provisional(confirmed: list, queue: dict, by_sym: dict) -> list:
    """Fill remaining slots (up to LEADERBOARD_PROVISIONAL_TARGET total) with
    top Deep-Dive Queue candidates as CLEARLY MARKED provisional entries.

    These are NOT a replacement for ACE curation - see the design discussion:
    a top-N-by-composite auto-list has no defense against the manual-only
    vetoes (V1/V2/V4/V6/V7) and no independent judgment for the
    divergence-drift alarm to check against. So a provisional entry's
    ace_score literally equals its mechanical composite (labeled as such,
    never disguised as a real ACE score), never gets a divergence baseline,
    and is never written back to leaderboard.json - it exists only in this
    run's published output, regenerated fresh each time from whatever
    currently tops the queue.
    """
    need = max(0, LEADERBOARD_PROVISIONAL_TARGET - len(confirmed))
    if need == 0:
        return []
    confirmed_syms = {e["symbol"] for e in confirmed}
    pool = []
    for lane_key, lane_label in (("lane1_volume_confirmed", "Lane 1 - volume confirmed"),
                                 ("lane2_watching", "Lane 2 - watching")):
        for entry in queue.get(lane_key, []):
            if entry["symbol"] in confirmed_syms:
                continue
            r = by_sym.get(entry["symbol"])
            if r:
                pool.append((r, lane_label))
    pool.sort(key=lambda t: -(t[0]["composite"] or 0))

    out = []
    for r, lane_label in pool[:need]:
        out.append({
            "symbol": r["symbol"], "name": r["name"], "provisional": True,
            "ace_score": r["composite"], "grade": r["grade"],
            "verdict": f"Mechanically qualified ({lane_label}) - no ACE deep-dive "
                       "done yet. Worksheet available in the Deep-Dive Queue tab.",
            "theme": None, "added": None, "pending_add": False,
            "cmp": r["cmp"], "chg_pct": r["chg_pct"],
            "mech_composite": r["composite"], "mech_grade": r["grade"],
            "divergence": None, "divergence_baseline": None,
            "divergence_drift": None, "re_review": False,
            "vetoes_now": r["vetoes_triggered"], "in_universe": True,
            "why": _why_for(r),
        })
    return out


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weekly", action="store_true", help="full fundamentals refresh")
    ap.add_argument("--force-publish", action="store_true",
                    help="override the degraded-run guard (use only deliberately)")
    args = ap.parse_args()

    now = datetime.now(IST)
    universe, uni_src = get_universe()
    # fix #9: leaderboard names must be scored every run even if they fall out
    # of the index or liquidity screen - the MOST alarming scenario (a board
    # name disappearing) must produce the MOST alarm, not silence.
    lb_all = (json.loads(LEADERBOARD_FILE.read_text()).get("entries", [])
              if LEADERBOARD_FILE.exists() else [])
    board_only = [e for e in lb_all if e["symbol"] not in universe]
    for e in board_only:
        universe[e["symbol"]] = {"name": e.get("name", e["symbol"]),
                                 "index": "board-only"}
    if board_only:
        print(f"[main] force-following {len(board_only)} board-only names: "
              f"{[e['symbol'] for e in board_only]}")
    syms = sorted(universe)
    print(f"[main] universe {len(syms)} symbols ({uni_src})")

    funds, fund_src = load_fundamentals(syms, weekly=args.weekly)
    ghosts = ghost_symbols(set(syms))          # fix #2: keep following exits
    if ghosts:
        print(f"[main] following {len(ghosts)} ex-universe symbols for attribution")
    hists = fetch_price_history(syms + ghosts)

    # ---- fix #3: degraded-run guard, stage 1 (fetch layer). A rate-limit storm
    # is a data failure, not a market event: refuse to score on a gutted feed.
    hist_ratio = len([s for s in syms if s in hists]) / max(1, len(syms))
    if hist_ratio < RUN_GUARD["min_hist_ratio"] and not args.force_publish:
        print(f"[GUARD] only {hist_ratio:.0%} of universe has price history "
              f"(< {RUN_GUARD['min_hist_ratio']:.0%}). Refusing to publish - "
              "nothing written. Re-run later or use --force-publish.")
        return 2

    # ---- regime (#7): live universe median PE - valuation is relative, not absolute
    pes = sorted(f["pe"] for f in funds.values()
                 if f and f.get("pe") and 0 < f["pe"] < 300)
    regime = {"universe_pe_median": round(pes[len(pes) // 2], 1) if pes else None,
              **market_regime(),
              "note": "valuation scored vs universe median PE; Nifty trend + "
                      "India VIX shown as orthogonal context (fix #10)"}
    print(f"[main] regime: median PE {regime['universe_pe_median']} · "
          f"Nifty>200DMA {regime['nifty_above_200dma']} · VIX {regime['india_vix']}")

    rows, excl_fund, excl_liquid, excl_coverage = [], 0, 0, 0
    for sym in syms:
        f = funds.get(sym)
        if not has_min_data(f):
            excl_fund += 1
            continue
        hist = hists.get(sym)
        tech = tech_context(hist)
        if (tech["turnover_cr"] is not None and tech["turnover_cr"] < LIQUIDITY_FLOOR_CR
                and universe[sym]["index"] != "board-only"):
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

    # ---- fix #3 stage 2: compare against the last published run before writing
    prev_file = DOCS_DATA / "primary_latest.json"
    if prev_file.exists() and not args.force_publish:
        try:
            prev_scored = json.loads(prev_file.read_text()).get("scored", 0)
        except Exception:
            prev_scored = 0
        if (prev_scored >= RUN_GUARD["min_scored_abs"]
                and len(rows) < prev_scored * RUN_GUARD["min_scored_ratio_vs_prev"]):
            print(f"[GUARD] scored {len(rows)} vs {prev_scored} last run "
                  f"(< {RUN_GUARD['min_scored_ratio_vs_prev']:.0%}). This is a data "
                  "failure, not a market event. Nothing written - tabs, watch-state "
                  "and score log untouched. Re-run later or use --force-publish.")
            return 2

    # ---- attribution (#1/#2): split-safe, survivorship-aware
    attribution = compute_attribution(hists)

    # ---- bear case (#8) on every published row
    for r in rows:
        r["bear_case"] = bear_case(r, regime["universe_pe_median"])

    criteria = {
        "universe": "Nifty Midcap 150 + Smallcap 250 (primary) · + Microcap 250 (radar only)",
        "hard_filters": [f"NSE EQ series · 20d avg turnover >= Rs {LIQUIDITY_FLOOR_CR}cr",
                         "5+ quarters of reported financials (missing data = excluded, never guessed)",
                         f"framework coverage >= {int(MIN_COVERAGE*100)}% or excluded"],
        "framework_weights": FRAMEWORK_WEIGHTS,
        "grade_bands": "A+ >=9.0 · A 8.5 · A- 8.0 · B+ 7.5 · B 7.0 · B- 6.5 · below -> C/D/F",
        "vetoes_automated": [f"V3: D/E > {V3_DE_LIMIT} with declining CFO (caps grade at C)"],
        "vetoes_manual": list(MANUAL_VETOES.keys()),
        "valuation_regime": "PE scored relative to live universe median, not absolute",
        "queue_rules": f"composite >= {QUEUE['min_composite']}, no vetoes, not on leaderboard; Lane 1 = RVol >= {QUEUE['lane1_rvol']}x on an up day",
        "radar_gates": (f"universe: Smallcap 250 + Microcap 250 · mcap <= {MULTIBAGGER['max_mcap_cr']}cr · "
                        f"rev CAGR >= {MULTIBAGGER['min_rev_cagr3y_pct']}% · PAT CAGR >= {MULTIBAGGER['min_pat_cagr3y_pct']}% · "
                        f"D/E <= {MULTIBAGGER['max_de']} · ROE >= {MULTIBAGGER['min_roe_pct']}% · above 200DMA · "
                        f"NOT over-extended (<= {RADAR_EXTENSION['max_dma200_ext_pct']:.0f}% above 200DMA, "
                        f"<= {RADAR_EXTENSION['max_ret6m_pct']:.0f}% 6m return) · "
                        f"framework coverage >= {int(RADAR_EXTENSION['min_coverage']*100)}% · "
                        f"volume integrity ({INTEGRITY['min_sessions']}+ sessions, no volume concentration, "
                        "no zero-volume days) · no vetoes · signal age tracked, stale after "
                        f"{RADAR_EXTENSION['stale_after_runs']} sessions"),
        "data_honesty": (f"prices split/bonus-adjusted · coverage penalty -{COVERAGE_PENALTY} x missing share · "
                         "promoter % display-only (yfinance field is not SEBI promoter category - verify NSE SHP) · "
                         "degraded runs refuse to publish · attribution follows exits and counts unmatched names"),
        "leaderboard": f"manual ACE deep-dives only, entry bar {LEADERBOARD_CUTOFF}, max {LEADERBOARD_MAX}",
    }
    meta = {
        "generated_at": now.isoformat(), "trade_date": trade_date,
        "criteria": criteria,
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
    # ---- TAB 1: Primary Screener (Midcap 150 + Smallcap 250; microcap is radar-only)
    primary_rows = [r for r in rows if r["index"] in PRIMARY_INDICES][:TOP_N_PER_TAB * 2]
    suggested_weights(primary_rows)                      # (#9)
    conc = sector_concentration(primary_rows[:TOP_N_PER_TAB])
    corr = correlation_flags(primary_rows, hists)
    tab_members["primary"] = [r["symbol"] for r in primary_rows[:TOP_N_PER_TAB]]
    write_tab("primary", primary_rows, {**meta,
              "portfolio": {"sector_concentration": conc,
                            "correlation_flags": corr,
                            "sizing_note": "inverse-volatility weights, "
                                           "capped at 8% - risk illustration only"}})
    print(f"[main] primary: {len(primary_rows)} cards, "
          f"leader {primary_rows[0]['symbol'] if primary_rows else '-'}, "
          f"{len(conc['warnings'])} concentration warnings")

    # ---- TAB 3 (part 1): confirmed Elite Leaderboard entries (manual ACE +
    #      live CMP) - built in-memory first so queue can exclude them; the
    #      file itself is written LATER, after provisional fill is computed
    confirmed_lb = build_confirmed_leaderboard(by_sym)
    lb_syms = confirmed_lb["syms"]
    print(f"[main] leaderboard: {len(confirmed_lb['entries'])} confirmed (manual ACE)")

    # ---- TAB 2: Deep-Dive Queue
    queue = build_queue(rows, lb_syms)
    # automation #1: auto-drafted ACE worksheet for every queue candidate -
    # mechanical prep only, qualitative sections stay blank by design
    for lane_key, lane_name in (("lane1_volume_confirmed", "lane1"),
                                ("lane2_watching", "lane2")):
        for entry in queue[lane_key]:
            r = by_sym.get(entry["symbol"])
            if r:
                entry["ace_worksheet"] = draft_worksheet(r, rows, lane=lane_name)
    enrich_queue(queue)   # Section 8 upgrade: grounded, cited research for
                          # Lane 1 only, server-side, degrades gracefully if
                          # ANTHROPIC_API_KEY secret is not set
    (DOCS_DATA / "queue_latest.json").write_text(
        json.dumps({**meta, "tab": "queue", **queue}, indent=0))
    print(f"[main] queue: lane1 {len(queue['lane1_volume_confirmed'])}, "
          f"lane2 {len(queue['lane2_watching'])} (worksheets attached)")

    # ---- TAB 3 (part 2): fill remaining leaderboard slots with clearly
    # marked provisional entries from the queue, then write the final file
    provisional = fill_provisional(confirmed_lb["entries"], queue, by_sym)
    combined = sorted(confirmed_lb["entries"] + provisional,
                      key=lambda e: -(e.get("ace_score") or 0))
    leaderboard = {k: v for k, v in confirmed_lb.items() if k != "syms"}
    leaderboard.update({"entries": combined,
                        "confirmed_count": len(confirmed_lb["entries"]),
                        "provisional_count": len(provisional)})
    (DOCS_DATA / "leaderboard_latest.json").write_text(
        json.dumps({**meta, "tab": "leaderboard", **leaderboard}, indent=0))
    print(f"[main] leaderboard finalized: {len(confirmed_lb['entries'])} confirmed + "
          f"{len(provisional)} provisional = {len(combined)} shown")

    # automation #2: digest of what's NEW in the queue since last run
    digest_md, digest_state = build_digest(queue, trade_date)
    (DOCS_DATA / "digest_latest.md").write_text(digest_md)
    save_state(digest_state)
    print("[main] digest written")

    radar, near = [], []
    for r in rows:
        if r["index"] not in MULTIBAGGER["eligible_indices"]:
            continue
        ok, fails = multibagger_pass(r)
        if ok:
            radar.append(r)
        elif len(fails) == 1 and fails[0] not in ("hard veto", "volume integrity"):
            near.append({"symbol": r["symbol"], "name": r["name"],
                         "composite": r["composite"], "near_miss": fails[0]})
    radar = radar[:MULTIBAGGER["max_names"]]
    # fix #6: signal age - day 2 on the radar is discovery, day 40 is a stale
    # signal that probably already ran. First-cleared date persists per name.
    radar_state_file = DATA / "radar_state.json"
    rstate = json.loads(radar_state_file.read_text()) if radar_state_file.exists() else {}
    for r in radar:
        st = rstate.get(r["symbol"]) or {"first_seen": trade_date, "runs": 0}
        st["runs"] += 1
        rstate[r["symbol"]] = st
        r["radar_age"] = {"first_seen": st["first_seen"], "runs": st["runs"],
                          "stale": st["runs"] > RADAR_EXTENSION["stale_after_runs"]}
    rstate = {s: v for s, v in rstate.items() if s in {r["symbol"] for r in radar}}
    radar_state_file.write_text(json.dumps(rstate, indent=0))
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
    for e in lb_all:                                   # fix #9: silence is the alarm
        if e["symbol"] not in by_sym:
            exits["names"].append({"symbol": e["symbol"], "name": e.get("name"),
                "entered": e.get("added"), "entry_composite": None,
                "current_composite": None, "grade": "-",
                "reasons": ["BOARD NAME NOT SCOREABLE THIS RUN - no price/fundamental "
                            "data retrievable. Verify listing status, corporate "
                            "actions, and liquidity IMMEDIATELY."]})
            exits["count"] += 1
    print(f"[main] exit review: {exits['count']} names flagged")
    exit_payload = {**meta, "exit_review": exits}
    (DOCS_DATA / "system_latest.json").write_text(json.dumps(exit_payload, indent=0))

    # ---- log today for future attribution (#1)
    log_run(rows, trade_date)
    return 0


if __name__ == "__main__":
    sys.exit(main())
