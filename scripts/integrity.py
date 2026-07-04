"""Volume-integrity checks (critique #5): defense against manufactured volume.

RVol/U-D style signals are exactly what circular trading or a small pump is
designed to produce. Before any pre-run signal in a thin name is trusted:
  - enough listed trading history (min_sessions),
  - volume not concentrated in a handful of days,
  - no zero-volume sessions in the recent window.
Failures become card flags everywhere and HARD GATES on the Multibagger Radar.
"""
from __future__ import annotations
import pandas as pd

from config import INTEGRITY


def integrity_check(hist: pd.DataFrame | None) -> dict:
    out = {"sessions": 0, "top3_vol_share": None, "zero_vol_days_60d": None,
           "flags": [], "radar_ok": False}
    if hist is None or "Volume" not in hist.columns:
        out["flags"].append("no trading history available")
        return out

    vol = hist["Volume"].dropna()
    out["sessions"] = int(len(vol))
    if out["sessions"] < INTEGRITY["min_sessions"]:
        out["flags"].append(f"only {out['sessions']} sessions of history "
                            f"(< {INTEGRITY['min_sessions']}) - recently listed, "
                            "pre-run signals unreliable")

    last20 = vol.tail(20)
    if len(last20) >= 15 and float(last20.sum()) > 0:
        share = float(last20.nlargest(3).sum() / last20.sum())
        out["top3_vol_share"] = round(share, 2)
        if share > INTEGRITY["max_top3_vol_share"]:
            out["flags"].append(f"{share:.0%} of 20d volume came from 3 sessions - "
                                "volume concentration consistent with manufactured activity")

    last60 = vol.tail(60)
    zero = int((last60 <= 0).sum())
    out["zero_vol_days_60d"] = zero
    if zero > INTEGRITY["max_zero_vol_days_60d"]:
        out["flags"].append(f"{zero} zero-volume day(s) in 60 sessions - too thin to trust")

    out["radar_ok"] = (out["sessions"] >= INTEGRITY["min_sessions"]
                       and (out["top3_vol_share"] is None
                            or out["top3_vol_share"] <= INTEGRITY["max_top3_vol_share"])
                       and zero <= INTEGRITY["max_zero_vol_days_60d"])
    return out
