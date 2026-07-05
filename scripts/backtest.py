"""Point-in-time backtest of the Elite Screener scoring engine.

WHAT THIS IS: the same scoring engine (8 frameworks, vetoes, coverage
penalty, radar gates) run AS-OF a past date, using only information that was
plausibly knowable then, with actual forward returns measured from the same
split-adjusted price series. It answers: "would the grades have separated
winners from losers?"

HOW POINT-IN-TIME IS ENFORCED:
- Prices/technicals: the adjusted history is TRUNCATED at the as-of date.
  Nothing after it exists for scoring.
- Fundamentals: only statements whose period-end is at least REPORT_LAG_DAYS
  (90) before the as-of date are "known". Ratios (ROE, margins, D/E) are
  recomputed from those dated statements - today's convenience fields
  (info.roe etc.) are NOT used because they embed today's knowledge.
- PE as-of: price_asof / EPS from the last known annual PAT, using today's
  share count (mcap_today / price_today). Share issuance between then and now
  is a named approximation.
- Fields that cannot be reconstructed (beta, dividend yield, promoter) are
  set to None; affected frameworks degrade and the coverage penalty applies -
  exactly as the live system treats missing data.

BIASES THAT REMAIN - NAMED, NOT HIDDEN (read `caveats` in every output):
1. UNIVERSE SURVIVORSHIP: today's index constituents are used because
   historical NSE membership files are not freely available. Stocks that
   cratered out of the index before today are invisible to the test, which
   biases ALL grade buckets upward. Grade SEPARATION (A vs C spread) remains
   meaningful; absolute return levels do not.
2. Statement availability: yfinance serves ~4 annual periods as restated
   today; restatements between then and now are invisible.
3. Report lag is modeled as a flat 90 days; real filing dates vary.

Usage (designed for the backtest.yml workflow; Yahoo unreachable locally):
  python scripts/backtest.py --asof 2025-10-01 --horizons 63,126
"""
from __future__ import annotations
import argparse, json, math, time
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from config import (DATA, DOCS_DATA, YF_SUFFIX, LIQUIDITY_FLOOR_CR,
                    MIN_COVERAGE, FRAMEWORK_WEIGHTS)
from universe import get_universe
from datafeed import _extract_one, FUND_CACHE
from technical import tech_context
from frameworks import score_all
from vetoes import check_vetoes
from integrity import integrity_check
from attribution import _spearman
from build_screener import multibagger_pass, build_row

REPORT_LAG_DAYS = 90

CAVEATS = [
    "Universe = TODAY's index constituents (historical membership not freely "
    "available): survivorship bias lifts ALL buckets - trust the A-vs-C "
    "SEPARATION, not absolute returns.",
    f"Fundamentals: only statements ended >= {REPORT_LAG_DAYS} days before "
    "as-of date are used; restatements since are invisible.",
    "PE/mcap as-of use today's share count (issuance since is a named "
    "approximation). Beta/dividend/promoter not reconstructable -> those "
    "framework components degrade with the standard coverage penalty.",
    "Quarterly-momentum legs are usually degraded at as-of dates > ~6 months "
    "back (only 5 quarters served) - Jhunjhunwala coverage drops accordingly.",
]


def _slice_by_dates(vals, dates, cutoff):
    """Keep newest-first entries whose statement date <= cutoff."""
    if not vals or not dates:
        return None
    out = [v for v, d in zip(vals, dates) if d and d <= cutoff]
    return out or None


def pit_fundamentals(f: dict, asof: str, px_asof: float, px_now: float) -> dict | None:
    """Reconstruct the fundamentals dict as plausibly known on `asof`."""
    cutoff = (datetime.fromisoformat(asof) - timedelta(days=REPORT_LAG_DAYS)
              ).strftime("%Y-%m-%d")
    a_dates, q_dates = f.get("a_dates") or [], f.get("q_dates") or []
    g = {
        "q_dates": [d for d in q_dates if d <= cutoff],
        "q_rev": _slice_by_dates(f.get("q_rev"), q_dates, cutoff),
        "q_pat": _slice_by_dates(f.get("q_pat"), q_dates, cutoff),
        "a_rev": _slice_by_dates(f.get("a_rev"), a_dates, cutoff),
        "a_pat": _slice_by_dates(f.get("a_pat"), a_dates, cutoff),
        "a_op":  _slice_by_dates(f.get("a_op"), a_dates, cutoff),
        # balance/cashflow assumed on the same fiscal calendar as income (std
        # for Indian cos) - sliced by the same dates
        "total_debt": _slice_by_dates(f.get("total_debt"), a_dates, cutoff),
        "equity": _slice_by_dates(f.get("equity"), a_dates, cutoff),
        "cash": _slice_by_dates(f.get("cash"), a_dates, cutoff),
        "cfo": _slice_by_dates(f.get("cfo"), a_dates, cutoff),
        "capex": _slice_by_dates(f.get("capex"), a_dates, cutoff),
        "beta": None, "div_yield": None, "promoter_pct": None,
        "sector": f.get("sector"),
    }
    if not g["a_rev"] or not g["a_pat"] or len(g["a_rev"]) < 2:
        return None
    # ratios recomputed from dated statements, never from today's info fields
    pat0, rev0 = g["a_pat"][0], g["a_rev"][0]
    eq0 = g["equity"][0] if g["equity"] else None
    op0 = g["a_op"][0] if g["a_op"] else None
    g["roe"] = (pat0 / eq0) if (pat0 and eq0 and eq0 > 0) else None
    g["op_margin"] = (op0 / rev0) if (op0 and rev0 and rev0 > 0) else None
    g["profit_margin"] = (pat0 / rev0) if (pat0 and rev0 and rev0 > 0) else None
    # PE/mcap as-of via today's share count (named approximation)
    mcap_now, shares = f.get("mcap"), None
    if mcap_now and px_now:
        shares = mcap_now / px_now
    g["mcap"] = shares * px_asof if shares else None
    g["pe"] = (shares * px_asof / pat0) if (shares and pat0 and pat0 > 0) else None
    return g


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", required=True, help="YYYY-MM-DD backtest date")
    ap.add_argument("--horizons", default="63,126",
                    help="forward horizons in trading sessions, comma-separated")
    args = ap.parse_args()
    asof = args.asof
    horizons = [int(x) for x in args.horizons.split(",")]

    universe, uni_src = get_universe()
    syms = sorted(universe)
    print(f"[bt] universe {len(syms)} ({uni_src}) · as-of {asof}")

    # fundamentals: reuse cache, refetch entries missing dated statements
    cache = json.loads(FUND_CACHE.read_text()) if FUND_CACHE.exists() else {}
    need = [s for s in syms if s not in cache or not cache[s].get("a_dates")]
    print(f"[bt] fetching dated statements for {len(need)} symbols")
    for i, sym in enumerate(need):
        got = _extract_one(sym)
        if got:
            cache[sym] = got
        if (i + 1) % 50 == 0:
            print(f"[bt] {i+1}/{len(need)}")
        time.sleep(0.35)
    FUND_CACHE.write_text(json.dumps(cache, indent=0))

    # 3y adjusted prices, chunked
    hists: dict[str, pd.DataFrame] = {}
    for i in range(0, len(syms), 50):
        batch = [s + YF_SUFFIX for s in syms[i:i + 50]]
        try:
            df = yf.download(batch, period="3y", interval="1d",
                             group_by="ticker", auto_adjust=True,
                             progress=False, threads=True)
        except Exception as e:
            print(f"[bt] batch {i//50} failed: {e}")
            continue
        for s in syms[i:i + 50]:
            try:
                h = df[s + YF_SUFFIX].dropna(how="all")
                if len(h) > 260:
                    hists[s] = h
            except Exception:
                pass
        time.sleep(1.0)
    print(f"[bt] histories: {len(hists)}/{len(syms)}")

    cut = pd.Timestamp(asof)
    results, excl = [], {"no_pit_fundamentals": 0, "no_history": 0,
                         "illiquid": 0, "low_coverage": 0}
    pes = []
    pit_cache = {}
    for sym in syms:
        h = hists.get(sym)
        if h is None or (h.index <= cut).sum() < 210:
            excl["no_history"] += 1
            continue
        past = h[h.index <= cut]
        px_asof, px_now = float(past["Close"].iloc[-1]), float(h["Close"].iloc[-1])
        f = cache.get(sym)
        g = pit_fundamentals(f, asof, px_asof, px_now) if f else None
        if g is None:
            excl["no_pit_fundamentals"] += 1
            continue
        pit_cache[sym] = (g, past, h)
        if g.get("pe") and 0 < g["pe"] < 300:
            pes.append(g["pe"])
    pes.sort()
    regime = {"universe_pe_median": round(pes[len(pes) // 2], 1) if pes else None}
    print(f"[bt] PIT median PE: {regime['universe_pe_median']}")

    for sym, (g, past, h) in pit_cache.items():
        tech = tech_context(past)
        if tech["turnover_cr"] is not None and tech["turnover_cr"] < LIQUIDITY_FLOOR_CR:
            excl["illiquid"] += 1
            continue
        scored = score_all(g, tech, regime)
        if scored["composite"] is None:
            excl["no_pit_fundamentals"] += 1
            continue
        if scored["coverage"] < MIN_COVERAGE:
            excl["low_coverage"] += 1
            continue
        row = build_row(sym, universe[sym], g, tech, scored,
                        integrity_check(past))
        # forward returns from the SAME adjusted series
        future = h[h.index > cut]["Close"].dropna()
        fwd = {}
        for hz in horizons:
            if len(future) >= hz:
                fwd[str(hz)] = round((float(future.iloc[hz - 1]) / past["Close"].iloc[-1]
                                      - 1) * 100, 1)
        radar_ok, radar_fails = multibagger_pass(row)
        results.append({"symbol": sym, "index": row["index"],
                        "composite": row["composite"], "grade": row["grade"],
                        "coverage": row["coverage"],
                        "frameworks": row["frameworks"],
                        "vetoed": bool(row["vetoes_triggered"]),
                        "radar_pick": radar_ok, "fwd": fwd})

    # ---- aggregate: grade buckets + framework IC per horizon
    report = {"asof": asof, "generated": datetime.now().isoformat()[:19],
              "universe_source": uni_src, "scored": len(results),
              "exclusions": excl, "pit_median_pe": regime["universe_pe_median"],
              "caveats": CAVEATS, "horizons": {}}
    for hz in horizons:
        k = str(hz)
        sub = [r for r in results if k in r["fwd"]]
        if len(sub) < 30:
            report["horizons"][k] = {"status": "too few stocks with forward data"}
            continue
        rets = sorted(r["fwd"][k] for r in sub)
        by_grade = {}
        for r in sub:
            by_grade.setdefault(r["grade"], []).append(r["fwd"][k])
        ic = {}
        for fw in FRAMEWORK_WEIGHTS:
            pairs = [(r["frameworks"].get(fw), r["fwd"][k]) for r in sub
                     if r["frameworks"].get(fw) is not None]
            if len(pairs) >= 30:
                ic[fw] = _spearman([p[0] for p in pairs], [p[1] for p in pairs])
        comp_pairs = [(r["composite"], r["fwd"][k]) for r in sub]
        radar = [r for r in sub if r["radar_pick"]]
        report["horizons"][k] = {
            "n": len(sub),
            "universe_median_ret_pct": rets[len(rets) // 2],
            "by_grade": {g: {"n": len(v),
                             "median_ret_pct": round(sorted(v)[len(v) // 2], 1)}
                         for g, v in sorted(by_grade.items()) if len(v) >= 5},
            "composite_ic": _spearman([p[0] for p in comp_pairs],
                                      [p[1] for p in comp_pairs]),
            "framework_ic": ic,
            "radar_picks": {"n": len(radar),
                            "symbols": [r["symbol"] for r in radar],
                            "median_ret_pct": (round(sorted(r["fwd"][k] for r in radar)
                                                     [len(radar) // 2], 1)
                                               if radar else None)},
        }

    out = DOCS_DATA / f"backtest_{asof}.json"
    out.write_text(json.dumps(report, indent=1))
    (DOCS_DATA / "backtest_latest.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items()
                      if k in ("scored", "exclusions", "horizons")}, indent=1)[:2000])
    print(f"[bt] written: {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
