"""Attribution engine v1.4 - the system's memory of itself, made honest.

Two corruption channels closed:

SPLIT SAFETY (fix #1): forward returns are no longer computed from prices
logged months ago vs raw prices today (a split in between fabricates a crash).
Both endpoints now come from the SAME split/bonus-adjusted price series
fetched this run: the adjusted close on the snapshot date vs the latest
adjusted close. Corporate actions cancel out by construction.

SURVIVORSHIP (fix #2): the score log's symbols are followed even after they
leave the universe - the pipeline fetches "ghost" histories for every symbol
that appears in recent snapshots. A stock that cratered and got delisted
still contributes its (terrible) return, measured to its last traded price
and counted as "terminated". Symbols with no retrievable history at all are
NOT silently dropped: they are counted and published as unmatched_n with an
explicit upward-bias warning, because a self-flattering evidence table is
worse than none.
"""
from __future__ import annotations
import json

import pandas as pd

from config import DATA, ATTRIBUTION_HORIZONS, FRAMEWORK_WEIGHTS

SCORE_LOG = DATA / "score_history.json"
FW_ORDER = list(FRAMEWORK_WEIGHTS)
MIN_STOCKS_FOR_STATS = 30
TERMINATED_AFTER_DAYS = 15          # last trade older than this = treated as terminal


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < MIN_STOCKS_FOR_STATS:
        return None
    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return round(num / (dx * dy), 3) if dx > 0 and dy > 0 else None


def log_run(rows: list, trade_date: str) -> None:
    log = json.loads(SCORE_LOG.read_text()) if SCORE_LOG.exists() else {}
    log[trade_date] = {
        r["symbol"]: {
            "c": r["composite"], "g": r["grade"], "p": r["cmp"],
            "f": [r["frameworks"].get(k) for k in FW_ORDER],
        } for r in rows if r["cmp"] is not None
    }
    for k in sorted(log)[:-400]:
        del log[k]
    SCORE_LOG.write_text(json.dumps(log, indent=0))


def ghost_symbols(current: set[str]) -> list[str]:
    """Symbols in recent snapshots that left the universe - keep following them."""
    if not SCORE_LOG.exists():
        return []
    log = json.loads(SCORE_LOG.read_text())
    horizon = max(ATTRIBUTION_HORIZONS.values()) + 10
    seen: set[str] = set()
    for d in sorted(log)[-horizon:]:
        seen.update(log[d])
    return sorted(seen - current)


def _ret_from_series(closes: pd.Series, snap_date: str):
    """(return_pct, terminated) using the adjusted series only; (None, False)
    if no bar exists on/after the snapshot date."""
    idx = closes.index
    pos = idx.searchsorted(pd.Timestamp(snap_date))
    if pos >= len(closes):
        return None, False
    p0, p1 = float(closes.iloc[pos]), float(closes.iloc[-1])
    if p0 <= 0:
        return None, False
    try:
        days_stale = (pd.Timestamp.now(tz=idx[-1].tz) - idx[-1]).days
    except Exception:
        days_stale = 0
    return (p1 / p0 - 1) * 100, days_stale > TERMINATED_AFTER_DAYS


def compute_attribution(hists: dict[str, pd.DataFrame]) -> dict:
    """Grade-band forward returns + per-framework IC from ADJUSTED prices."""
    if not SCORE_LOG.exists():
        return {"status": "no history yet"}
    log = json.loads(SCORE_LOG.read_text())
    dates = sorted(log)
    out = {"status": "ok", "sessions_logged": len(dates), "horizons": {},
           "method": "both return endpoints from this run's split-adjusted series; "
                     "delisted/exited names measured to last trade (terminated); "
                     "unmatched names counted, never silently dropped"}

    closes_cache: dict[str, pd.Series] = {}
    for sym, h in hists.items():
        if h is not None and not h.empty and "Close" in h:
            c = h["Close"].dropna()
            if len(c):
                closes_cache[sym] = c

    for label, back in ATTRIBUTION_HORIZONS.items():
        if len(dates) <= back:
            out["horizons"][label] = {"status": f"insufficient history "
                                                f"({len(dates)}/{back + 1} sessions)"}
            continue
        snap_date = dates[-1 - back]
        snap = log[snap_date]
        rets, by_grade, unmatched, terminated_n = [], {}, [], 0
        fw_pairs = {k: ([], []) for k in FW_ORDER}
        for sym, rec in snap.items():
            closes = closes_cache.get(sym)
            if closes is None:
                unmatched.append(sym)
                continue
            ret, terminated = _ret_from_series(closes, snap_date)
            if ret is None:
                unmatched.append(sym)
                continue
            terminated_n += int(terminated)
            rets.append(ret)
            by_grade.setdefault(rec["g"], []).append(ret)
            fvals = rec.get("f") or []
            for i, k in enumerate(FW_ORDER):
                if i < len(fvals) and fvals[i] is not None:
                    fw_pairs[k][0].append(fvals[i])
                    fw_pairs[k][1].append(ret)
        if len(rets) < MIN_STOCKS_FOR_STATS:
            out["horizons"][label] = {"status": "too few matched stocks",
                                      "matched": len(rets), "unmatched_n": len(unmatched)}
            continue
        med = sorted(rets)[len(rets) // 2]
        h = {
            "status": "ok", "n": len(rets), "asof": snap_date,
            "universe_median_ret_pct": round(med, 1),
            "terminated_n": terminated_n,
            "unmatched_n": len(unmatched),
            "by_grade": {g: {"n": len(v),
                             "median_ret_pct": round(sorted(v)[len(v) // 2], 1)}
                         for g, v in sorted(by_grade.items()) if len(v) >= 5},
            "framework_ic": {k: _spearman(x, y) for k, (x, y) in fw_pairs.items()},
        }
        if unmatched:
            h["bias_warning"] = (f"{len(unmatched)} scored names have no retrievable "
                                 "price history; medians exclude them and are "
                                 "therefore biased UPWARD (failures disappear first)")
        out["horizons"][label] = h
    return out
