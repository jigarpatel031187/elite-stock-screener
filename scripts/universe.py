"""Universe management: Nifty Midcap 150 + Smallcap 250 constituents.

Downloads official NSE index constituent CSVs, caches them in
data/universe.json, refreshes monthly. Falls back to the last good cache
if NSE is unreachable - never fails the run, never invents symbols.
"""
from __future__ import annotations
import io, json
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests

from config import NSE_INDEX_CSVS, UNIVERSE_CACHE, UNIVERSE_MAX_AGE_DAYS

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "text/csv,*/*",
    "Referer": "https://www.nseindia.com/",
}


def _download_index(url: str) -> dict[str, str] | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        col_sym = next(c for c in df.columns if c.strip().lower() == "symbol")
        col_name = next(c for c in df.columns if "company" in c.strip().lower())
        return {str(row[col_sym]).strip(): str(row[col_name]).strip()
                for _, row in df.iterrows()}
    except Exception as e:
        print(f"[universe] download failed {url}: {e}")
        return None


def get_universe() -> tuple[dict, str]:
    """Returns ({symbol: {name, index}}, source_note)."""
    cached = None
    if UNIVERSE_CACHE.exists():
        cached = json.loads(UNIVERSE_CACHE.read_text())
        asof = datetime.fromisoformat(cached["asof"])
        if datetime.now(timezone.utc) - asof < timedelta(days=UNIVERSE_MAX_AGE_DAYS):
            return cached["symbols"], f"cache ({cached['asof'][:10]})"

    symbols: dict[str, dict] = {}
    ok = True
    for index_name, url in NSE_INDEX_CSVS.items():
        got = _download_index(url)
        if got is None:
            ok = False
            break
        for sym, name in got.items():
            symbols[sym] = {"name": name, "index": index_name}

    if ok and symbols:
        UNIVERSE_CACHE.write_text(json.dumps({
            "asof": datetime.now(timezone.utc).isoformat(),
            "symbols": symbols,
        }, indent=1))
        return symbols, "NSE index CSVs (fresh)"

    if cached:
        return cached["symbols"], f"STALE cache ({cached['asof'][:10]}) - NSE unreachable"
    raise RuntimeError("No universe available: NSE unreachable and no cache exists")
