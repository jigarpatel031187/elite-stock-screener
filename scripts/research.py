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
"""
from __future__ import annotations
import json
import os

import requests

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-5"
MAX_CANDIDATES_PER_RUN = 5   # cost control - Lane 1 is usually small anyway

SYSTEM = (
    "You are compiling due-diligence research on an Indian NSE-listed stock "
    "for a mutual fund distributor's internal worksheet. For each of the 6 "
    "topics given, search the web and report ONLY what you can find and cite. "
    "Paraphrase everything in your own words - never quote source text "
    "verbatim beyond a few words. If you find nothing reliable on a topic, "
    "say exactly that - do not infer, guess, or fill the gap with general "
    "knowledge about the company or sector. Every claim needs a source name "
    "and URL. Output ONLY valid JSON, no preamble, in this exact shape: "
    '{"findings": [{"topic": "...", "summary": "...", '
    '"sources": [{"title": "...", "url": "..."}], '
    '"status": "found" | "nothing_reliable_found"}]}'
)


def _call_claude(sym: str, name: str, queries: list[str], api_key: str) -> dict | None:
    prompt = (f"Company: {name} (NSE: {sym}). Research these 6 topics using web "
              f"search, one finding object per topic in the same order:\n" +
              "\n".join(f"{i+1}. {q}" for i, q in enumerate(queries)))
    try:
        r = requests.post(API_URL,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                    "content-type": "application/json"},
            json={"model": MODEL, "max_tokens": 2000, "system": SYSTEM,
                 "messages": [{"role": "user", "content": prompt}],
                 "tools": [{"type": "web_search_20250305", "name": "web_search"}]},
            timeout=90)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[research] {sym}: API call failed: {e}")
        return None

    text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    raw = "".join(text_blocks).strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(raw)
        if "findings" in parsed and isinstance(parsed["findings"], list):
            return parsed
    except json.JSONDecodeError:
        pass
    print(f"[research] {sym}: response was not valid JSON, discarding "
          f"(no fabricated fallback)")
    return None


def enrich_section8(worksheet: dict, sym: str, name: str) -> None:
    """Mutates worksheet's section 8 in place, ADDING grounded findings
    alongside the original query list - never replacing the honest fallback."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    sec8 = next(s for s in worksheet["sections"] if s["n"] == 8)
    if not api_key:
        sec8["data"]["ai_research"] = {
            "status": "not_configured",
            "note": "Set the ANTHROPIC_API_KEY repository secret to enable "
                    "auto-compiled, cited research here. Manual queries above "
                    "still work without it."}
        return
    result = _call_claude(sym, name, sec8["data"]["queries"], api_key)
    if result is None:
        sec8["data"]["ai_research"] = {
            "status": "unavailable_this_run",
            "note": "Search/compile failed or returned unusable output - "
                    "use the manual queries above."}
        return
    sec8["data"]["ai_research"] = {"status": "ok", "findings": result["findings"]}
    sec8["status"] = "PARTIAL-AUTO"


def enrich_queue(queue: dict) -> None:
    """Call only for Lane 1, capped, to keep API spend predictable and small."""
    lane1 = queue.get("lane1_volume_confirmed", [])[:MAX_CANDIDATES_PER_RUN]
    for entry in lane1:
        ws = entry.get("ace_worksheet")
        if ws:
            enrich_section8(ws, entry["symbol"], entry["name"])
    if len(queue.get("lane1_volume_confirmed", [])) > MAX_CANDIDATES_PER_RUN:
        print(f"[research] capped at {MAX_CANDIDATES_PER_RUN}/"
              f"{len(queue['lane1_volume_confirmed'])} Lane 1 names this run")
