"""Attribution engine (critiques #1 and #2): the system's memory of itself.

Every run logs each stock's composite, grade, price, and the 8 framework
scores. On every subsequent run this module looks back 1m/3m/6m and answers:
  - Did higher grade bands actually deliver higher forward returns?
  - Which frameworks carried predictive weight? (Spearman rank IC of each
    framework's score against forward return.)

Nothing here changes the scores - it publishes the evidence so weight changes
are made from data, not aesthetics. Results are marked "insufficient data"
until enough history accumulates; they are never extrapolated.
"""
from __future__ import annotations
import json

from config import DATA, ATTRIBUTION_HORIZONS, FRAMEWORK_WEIGHTS

SCORE_LOG = DATA / "score_history.json"
FW_ORDER = list(FRAMEWORK_WEIGHTS)          # stable order for compact logging
MIN_STOCKS_FOR_STATS = 30


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rank correlation, no scipy dependency."""
    n = len(xs)
    if n < MIN_STOCKS_FOR_STATS:
        return None
    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:                          # average ranks for ties
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


def compute_attribution(current_prices: dict[str, float]) -> dict:
    """Grade-band forward returns + per-framework IC for each horizon."""
    if not SCORE_LOG.exists():
        return {"status": "no history yet"}
    log = json.loads(SCORE_LOG.read_text())
    dates = sorted(log)
    out = {"status": "ok", "sessions_logged": len(dates), "horizons": {}}

    for label, back in ATTRIBUTION_HORIZONS.items():
        if len(dates) <= back:
            out["horizons"][label] = {"status": f"insufficient history "
                                                f"({len(dates)}/{back + 1} sessions)"}
            continue
        snap = log[dates[-1 - back]]
        rets, by_grade, fw_pairs = [], {}, {k: ([], []) for k in FW_ORDER}
        for sym, rec in snap.items():
            p0, p1 = rec.get("p"), current_prices.get(sym)
            if not p0 or not p1:
                continue
            ret = (p1 / p0 - 1) * 100
            rets.append(ret)
            by_grade.setdefault(rec["g"], []).append(ret)
            for i, k in enumerate(FW_ORDER):
                fv = (rec.get("f") or [None] * 8)[i] if rec.get("f") else None
                if fv is not None:
                    fw_pairs[k][0].append(fv)
                    fw_pairs[k][1].append(ret)
        if len(rets) < MIN_STOCKS_FOR_STATS:
            out["horizons"][label] = {"status": "too few matched stocks"}
            continue
        med = sorted(rets)[len(rets) // 2]
        out["horizons"][label] = {
            "status": "ok", "n": len(rets), "asof": dates[-1 - back],
            "universe_median_ret_pct": round(med, 1),
            "by_grade": {g: {"n": len(v),
                             "median_ret_pct": round(sorted(v)[len(v) // 2], 1)}
                         for g, v in sorted(by_grade.items()) if len(v) >= 5},
            "framework_ic": {k: _spearman(x, y) for k, (x, y) in fw_pairs.items()},
            "note": "IC = Spearman rank correlation of framework score vs forward "
                    "return. Persistent IC near zero for a framework is evidence "
                    "its weight is decorative.",
        }
    return out
