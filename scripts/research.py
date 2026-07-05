"""Section 8 upgrade: grounded, cited compilation of the 6 mandatory searches.

RUNS SERVER-SIDE ONLY, inside the GitHub Actions pipeline, using a GitHub
encrypted secret (ANTHROPIC_API_KEY). It is never called from the browser and
the key never appears in any file this repo publishes - a public GitHub Pages
site has no backend, so any client-side key would be visible to every visitor
via dev tools. That is a real leak, not a theoretical one, which is why this
module exists instead of a key-entry field in the HTML.

WHAT THIS DOES: for each Lane 1 candidate (volume-confirmed - the ones worth
spending API budget on), calls the Claude API with the web_search tool and
asks it to research the same 6 topics the manual worksheet already lists as
queries, returning a citation for every claim. This is COMPILATION - finding
and summarizing what public sources say - not the qualitative judgment layer
(moat conviction, management candor read) that Section 9 still reserves for
you, deliberately.

FAILURE MODE, BY DESIGN: if the key is missing, the API errors, or search
returns nothing useful, the worksheet falls back to the plain query-string
list (the original Section 8) rather than ever fabricating a finding. A
missing citation is an honest gap; an invented one is a landmine.

COST CONTROLS (this revision):
- Model downgraded to Haiku. This module is a compilation task - search,
  summarize, cite, output fixed JSON - not open-ended reasoning, and that is
  squarely in Haiku's lane at a fraction of Sonnet's per-token cost.
- Results are cached per symbol in data/research_cache.json and only
  refreshed every STALE_DAYS days. Moat narrative, governance, and
  management track record don't change day to day, so re-researching an
  unchanged Lane 1 name on every single run (the old behaviour - this ran
  unconditionally Mon-Sat) was pure waste. A cached entry still renders on
  the worksheet every run, with an honest "researched N days ago" note - it
  just doesn't cost a fresh API call until it's actually due.
- MAX_CANDIDATES_PER_RUN now caps LIVE calls only. Names served from cache
  don't count against it, so cache coverage keeps growing across the week
  without growing spend - a stale name simply waits for the next free slot.
- web_search is capped with max_uses so the model can't silently run more
  searches per candidate than the 6 topics need.
- Prompt caching was evaluated and deliberately NOT added: the SYSTEM prompt
  below is a few hundred tokens, well under the minimum cacheable length on
  any current model (1,024 tokens on Sonnet, 4,096 tokens on Haiku). Adding
  cache_control here would be a no-op - Anthropic bills it as an ordinary
  uncached prompt - so it's left out rather than shipped as dead code. If
  SYSTEM ever grows past that threshold (e.g. more worked examples), this is
  worth revisiting.
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"   # compilation task - Haiku is plenty; see
                                       # module docstring before bumping back up
MAX_CANDIDATES_PER_RUN = 5   # cap on LIVE api calls per run (cache hits are free)
STALE_DAYS = 7               # don't re-research a name more than once a week
WEB_SEARCH_MAX_USES = 8      # 6 topics + a little headroom, never unbounded

ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "data" / "research_cache.json"

SYSTEM = (
    "You are compiling due-diligence research on an Indian NSE-listed stock "
    "for a mutual fund distributor's internal worksheet. For each of the 6 "
    "topics given, search the web and report ONLY what you can find and cite. "
    "Paraphrase everything in your own words - never quote source text "
    "verbatim beyond a few words. If you find nothing reliable on a topic, "
    "say exactly that - do not infer, guess, or fill the gap with general "
    "knowledge about the company or sector. Every claim needs a source name "
    "and URL.\n\n"
    "SEPARATELY, using only what your searches actually turned up (never "
    "prior knowledge about the company), draft a qualitative synthesis "
    "covering: moat narrative, management candor/track record, and "
    "governance red flags. This is an UNREVIEWED DRAFT for a human analyst "
    "to confirm, edit, or reject - not a final judgment. If your searches "
    "did not surface enough to say anything substantive on a point, say "
    "'insufficient public information found' rather than reasoning from "
    "general sector knowledge or the company's own promotional material "
    "uncritically. Flag if a source appears to be company-controlled "
    "(investor deck, PR) versus independent.\n\n"
    "Output ONLY valid JSON, no preamble, in this exact shape: "
    '{"findings": [{"topic": "...", "summary": "...", '
    '"sources": [{"title": "...", "url": "..."}], '
    '"status": "found" | "nothing_reliable_found"}], '
    '"qualitative_draft": {"moat_narrative": "...", '
    '"management_read": "...", "governance_notes": "...", '
    '"confidence": "low" | "medium" | "high", '
    '"caution": "one sentence on what this draft cannot tell you"}}'
)


# ---------------------------------------------------------------- cache I/O
def _load_cache() -> dict:
    """Never lets a bad cache file break the run - worst case on any error
    here is re-researching more names than strictly necessary, not a crash."""
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"[research] cache unreadable ({e}), starting fresh this run")
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, indent=2))
    except OSError as e:
        print(f"[research] could not write cache ({e}) - next run will just "
              "re-research more names than necessary, not fail")


def _days_since(iso_timestamp: str) -> float:
    then = datetime.fromisoformat(iso_timestamp)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds() / 86400


def _is_stale(sym: str, cache: dict) -> bool:
    entry = cache.get(sym)
    return entry is None or _days_since(entry["researched_at"]) >= STALE_DAYS


# ------------------------------------------------------------------ Claude API
def _call_claude(sym: str, name: str, queries: list[str], api_key: str) -> tuple[dict | None, str]:
    """Returns (result_or_None, status). status is one of:
    'ok', 'insufficient_credit', 'auth_error', 'rate_limited', 'other_error',
    'bad_response' - classified from what the API actually returned, never
    guessed."""
    prompt = (f"Company: {name} (NSE: {sym}). Research these 6 topics using web "
              f"search, one finding object per topic in the same order:\n" +
              "\n".join(f"{i+1}. {q}" for i, q in enumerate(queries)))
    try:
        r = requests.post(API_URL,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                    "content-type": "application/json"},
            json={"model": MODEL, "max_tokens": 2000, "system": SYSTEM,
                 "messages": [{"role": "user", "content": prompt}],
                 "tools": [{"type": "web_search_20250305", "name": "web_search",
                           "max_uses": WEB_SEARCH_MAX_USES}]},
            timeout=90)
        if r.status_code != 200:
            body = ""
            try:
                body = json.dumps(r.json())
            except Exception:
                body = r.text[:300]
            low = body.lower()
            print(f"[research] {sym}: HTTP {r.status_code}: {body[:300]}")
            if r.status_code == 401:
                return None, "auth_error"
            if r.status_code == 429:
                return None, "rate_limited"
            if r.status_code == 400 and ("credit balance" in low or "billing" in low
                                         or "quota" in low or "insufficient" in low):
                return None, "insufficient_credit"
            return None, "other_error"
        data = r.json()
    except Exception as e:
        print(f"[research] {sym}: request failed: {e}")
        return None, "other_error"

    text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    raw = "".join(text_blocks).strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(raw)
        if "findings" in parsed and isinstance(parsed["findings"], list):
            return parsed, "ok"
    except json.JSONDecodeError:
        pass
    print(f"[research] {sym}: response was not valid JSON, discarding "
          f"(no fabricated fallback)")
    return None, "bad_response"


# Display copy per status - exact, distinct messages as requested. Never
# invents specifics beyond what the API told us.
STATUS_MESSAGES = {
    "insufficient_credit": "⚠ API TOKEN RECHARGE FIRST — your Anthropic API "
        "credit balance is too low. Add credits at console.anthropic.com → "
        "Plans & Billing. Manual queries above still work meanwhile.",
    "auth_error": "⚠ API NOT WORKING — the ANTHROPIC_API_KEY secret was "
        "rejected (invalid or revoked). Check it in repo Settings → Secrets "
        "and variables → Actions.",
    "rate_limited": "⚠ API NOT WORKING — rate-limited by Anthropic this run. "
        "Will retry automatically next run.",
    "other_error": "⚠ API NOT WORKING — the research call failed this run "
        "(network or server error). Manual queries above still work.",
    "bad_response": "⚠ API NOT WORKING — the response could not be parsed "
        "as valid research output, so it was discarded rather than shown.",
}


# -------------------------------------------------------------- worksheet fill
def _apply_result(sec8: dict, sec9: dict, result: dict, researched_at: str,
                   overdue: bool = False) -> None:
    """Writes a research result - fresh or cached - into sections 8 and 9.
    Never fabricates: this only ever renders a result that genuinely came
    back from the API on this run or a prior one."""
    age = _days_since(researched_at)
    if overdue:
        freshness = (f"⏳ Cached research is {age:.0f}d old and overdue for "
                     f"refresh (target: every {STALE_DAYS}d) - will retry "
                     "automatically next run.")
    elif age < 1:
        freshness = "Freshly researched this run."
    else:
        freshness = f"Cached research from {age:.0f}d ago (refreshes every {STALE_DAYS}d)."

    sec8["data"]["ai_research"] = {"status": "ok", "findings": result["findings"],
                                    "freshness": freshness}
    sec8["status"] = "PARTIAL-AUTO"

    qd = result.get("qualitative_draft")
    if qd:
        sec9["data"]["ai_draft"] = {"status": "ok", **qd,
            "banner": "⚠ AI DRAFT — UNREVIEWED. This is a starting point built "
                      "only from what web search surfaced, not a judgment. "
                      "Confirm, edit, or reject before it informs any score.",
            "freshness": freshness}
    else:
        sec9["data"]["ai_draft"] = {"status": "bad_response",
            "note": "Model did not return a qualitative_draft section."}
    # Section 9's status is intentionally left MANUAL always - a draft
    # existing does not mean the qualitative layer has been reviewed. The
    # final ace_score/grade/verdict that goes into leaderboard.json is never
    # touched here or anywhere in this pipeline - that confirmation step
    # stays yours.


def _enrich_one(worksheet: dict, sym: str, name: str, cache: dict,
                 make_live_call: bool, budget_capped: bool) -> bool:
    """Fills worksheet sections 8 and 9 for one symbol. Returns True iff a
    live Claude API call was actually made this run (so enrich_queue can
    count it against MAX_CANDIDATES_PER_RUN)."""
    sec8 = next(s for s in worksheet["sections"] if s["n"] == 8)
    sec9 = next(s for s in worksheet["sections"] if s["n"] == 9)
    cached_entry = cache.get(sym)

    if not make_live_call:
        if cached_entry:
            _apply_result(sec8, sec9, cached_entry["result"],
                          cached_entry["researched_at"], overdue=budget_capped)
        elif budget_capped:
            note = (f"Fresh-research budget ({MAX_CANDIDATES_PER_RUN}/run) spent on "
                    "other Lane 1 names this run - picked up automatically next run.")
            sec8["data"]["ai_research"] = {"status": "budget_capped", "note": note}
            sec9["data"]["ai_draft"] = {"status": "budget_capped", "note": note}
        # else: not stale and nothing cached shouldn't happen (staleness with
        # an empty cache is always "stale"), but if it ever does, leave the
        # worksheet's existing manual-query default untouched.
        return False

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        if cached_entry:
            _apply_result(sec8, sec9, cached_entry["result"],
                          cached_entry["researched_at"], overdue=True)
        else:
            sec8["data"]["ai_research"] = {
                "status": "not_configured",
                "note": "Set the ANTHROPIC_API_KEY repository secret to enable "
                        "auto-compiled, cited research here. Manual queries above "
                        "still work without it."}
            sec9["data"]["ai_draft"] = {"status": "not_configured"}
        return False

    result, status = _call_claude(sym, name, sec8["data"]["queries"], api_key)
    if status != "ok" or result is None:
        note = STATUS_MESSAGES.get(status, STATUS_MESSAGES["other_error"])
        if cached_entry:
            # Today's refresh failed - show the last good research rather
            # than an error where working research already exists.
            _apply_result(sec8, sec9, cached_entry["result"],
                          cached_entry["researched_at"], overdue=True)
            sec8["data"]["ai_research"]["refresh_failed_note"] = note
        else:
            sec8["data"]["ai_research"] = {"status": status, "note": note}
            sec9["data"]["ai_draft"] = {"status": status, "note": note}
        return True   # still spent a live call against this run's budget

    researched_at = datetime.now(timezone.utc).isoformat()
    cache[sym] = {"researched_at": researched_at, "result": result}
    _apply_result(sec8, sec9, result, researched_at)
    return True


def enrich_section8(worksheet: dict, sym: str, name: str) -> None:
    """Back-compat single-symbol entry point (bypasses the cache/staleness
    layer - always makes a live call if a key is set). Kept for any external
    caller that still uses the old signature; enrich_queue below is the path
    the pipeline actually uses."""
    cache = _load_cache()
    _enrich_one(worksheet, sym, name, cache, make_live_call=True, budget_capped=False)
    _save_cache(cache)


def enrich_queue(queue: dict) -> None:
    """Call for every Lane 1 candidate. Live API calls are capped at
    MAX_CANDIDATES_PER_RUN; names already researched within STALE_DAYS are
    served from data/research_cache.json for free, so cache hits never eat
    into the fresh-call budget and coverage keeps growing across the week
    without growing spend."""
    lane1 = queue.get("lane1_volume_confirmed", [])
    cache = _load_cache()
    live_calls = 0
    for entry in lane1:
        ws = entry.get("ace_worksheet")
        if not ws:
            continue
        sym, name = entry["symbol"], entry["name"]
        stale = _is_stale(sym, cache)
        budget_capped = stale and live_calls >= MAX_CANDIDATES_PER_RUN
        made_call = _enrich_one(ws, sym, name, cache,
                                make_live_call=(stale and not budget_capped),
                                budget_capped=budget_capped)
        if made_call:
            live_calls += 1
    _save_cache(cache)
    print(f"[research] {live_calls} live API call(s) this run - "
          f"{len(lane1)} Lane 1 name(s) total, {len(cache)} symbol(s) cached overall")
