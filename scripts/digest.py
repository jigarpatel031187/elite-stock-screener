"""Deep-Dive Queue digest (automation part 2): the mechanical nudge that
replaces "did I remember to check the queue today."

Notification channel chosen deliberately: a GitHub Issue, updated each run,
using the workflow's own built-in GITHUB_TOKEN. This needs zero new secrets
(no WhatsApp/email API keys to provision) and GitHub already pushes its own
notifications (mobile app, email digest) for issue updates on repos you own -
so it's a real notification channel today, not a future integration.

The digest highlights DELTA (newly-confirmed / newly-entered names) over the
full list every run, so it stays a nudge instead of noise you learn to ignore.
"""
from __future__ import annotations
import json

from config import DATA

QUEUE_STATE = DATA / "queue_digest_state.json"


def build_digest(queue: dict, trade_date: str) -> tuple[str, dict]:
    """Returns (markdown_body, new_state). Diffs against the last run's queue
    membership to surface only what changed."""
    prev = json.loads(QUEUE_STATE.read_text()) if QUEUE_STATE.exists() else {"lane1": [], "lane2": []}
    lane1 = queue["lane1_volume_confirmed"]
    lane2 = queue["lane2_watching"]
    cur1, cur2 = {x["symbol"] for x in lane1}, {x["symbol"] for x in lane2}
    new1 = cur1 - set(prev.get("lane1", []))
    new2 = cur2 - set(prev.get("lane2", []))

    lines = [f"### Deep-Dive Queue — {trade_date}", ""]
    if new1:
        lines.append(f"**🟢 NEW in Lane 1 (volume-confirmed today)** — run these deep-dives first:")
        for x in lane1:
            if x["symbol"] in new1:
                lines.append(f"- **{x['symbol']}** ({x['name']}) — {x['grade']} {x['composite']}, "
                             f"RVol {x['rvol20']}x, ₹{x['cmp']} ({x['chg_pct']:+.1f}%) "
                             f"[worksheet ready in queue_latest.json]")
        lines.append("")
    else:
        lines.append("No new Lane 1 confirmations today.")
        lines.append("")

    if new2:
        lines.append(f"**🟡 New to Lane 2 (watching for volume):** " +
                     ", ".join(sorted(new2)))
        lines.append("")

    stale1 = set(prev.get("lane1", [])) - cur1
    if stale1:
        lines.append(f"**⚪ Left Lane 1** (composite fell below bar, vetoed, or "
                     f"volume no longer confirming): " + ", ".join(sorted(stale1)))
        lines.append("")

    lines.append(f"Current queue: Lane 1 = {len(lane1)}, Lane 2 = {len(lane2)}.")
    lines.append("A queue entry is an invitation to run the ACE deep-dive, never a buy signal.")
    lines.append("")
    lines.append("_Auto-updated by the Elite Screener pipeline. Full worksheets: "
                 "`docs/data/queue_latest.json` -> each candidate's `ace_worksheet`._")

    new_state = {"lane1": sorted(cur1), "lane2": sorted(cur2)}
    return "\n".join(lines), new_state


def save_state(state: dict) -> None:
    QUEUE_STATE.write_text(json.dumps(state, indent=0))
