# Elite Stock Screener — Midcap 150 · Smallcap 250 · Multibagger Radar

Automated, rule-based quality screener for Indian mid/small-cap equities.
Every stock in the Nifty Midcap 150 + Smallcap 250 universe is scored against
eight quantified investor frameworks (Elite Stock Analysis Engine v2.0 weights):

| Framework | Weight | Proxy measures |
|---|---|---|
| Buffett | 20% | ROE, operating margin, low D/E, positive FCF |
| Jhunjhunwala | 15% | Earnings momentum, PEG-style valuation, trend |
| Pabrai | 13% | Leverage, cheapness vs. history, net cash, drawdown value |
| Kacholia | 12% | 3y rev/PAT CAGR, operating leverage, size runway |
| Kedia | 12% | Small size, promoter holding, growth (SMILE proxy) |
| BlackRock | 10% | Liquidity, scale, ROE stability, beta |
| Vanguard | 10% | Profit consistency, low volatility, dividends |
| Damani | 8% | CFO/PAT conversion, margins, sane valuation |

Grades: A+ ≥9.0 · A 8.5 · A− 8.0 · B+ 7.5 · B 7.0 · B− 6.5 · below → C/D/F.

**Hard vetoes** (any one caps the grade at C):
- **V3 — automated**: D/E > 3 with declining operating cash flow.
- **V1 (SEBI/ED action), V2 (pledge >50%), V4 (qualified audit), V5 (promoter
  stake drop >10%/12m) — NOT automatable from free structured data.** They are
  shown as a manual-verification checklist on every card. This screener never
  silently marks them as passed.

**Multibagger Radar** is a separate, stricter gate on top of the graded
universe: mcap ≤ ₹25,000 cr, 3y revenue CAGR ≥ 15%, 3y PAT CAGR ≥ 18%,
D/E ≤ 0.6, ROE ≥ 15%, promoter ≥ 40% (flagged if unavailable), price above
200 DMA, no vetoes. If nothing qualifies, the tab stays empty by design.

**Honest framing.** Framework scores are quantified proxies of published
philosophies computed from *reported* data (yfinance statements). The
qualitative layers — moat narrative, management quality, scuttlebutt, the six
mandatory web searches of the chat-based Elite Engine deep-dive — are not
automated and are not pretended to be. Stocks missing 5 quarters of financials
are excluded and counted, never guessed. This repo is deliberately separate
from the Smart Money Ledger (momentum/accumulation system): different
philosophy, different codebase, different repo.

## One-time setup (~10 minutes)

1. **Create the repo.** github.com → **+ → New repository** → name it
   `elite-stock-screener` → **Public** (required for free Pages) → Create.
2. **Upload files.** "uploading an existing file" → drag this folder's full
   contents in (keep `.github/workflows/`, `scripts/`, `docs/`, `data/`,
   `requirements.txt`, `README.md`) → Commit.
   - If `.github` won't drag-drop: **Add file → Create new file**, name it
     `.github/workflows/screener.yml`, paste contents, commit.
3. **Enable Actions.** Actions tab → enable workflows if prompted.
4. **Workflow permissions.** Settings → Actions → General → Workflow
   permissions → **Read and write** → Save.
5. **Enable Pages.** Settings → Pages → Deploy from a branch → **main /docs**
   → Save. URL: `https://<username>.github.io/elite-stock-screener/`.
6. **First run.** Actions → *Elite screener update* → Run workflow → set
   `weekly` = `true`. First run takes **60–90 min** (builds the fundamentals
   cache for ~400 stocks). Daily runs after that take ~10–15 min.

## Schedule

- **Mon–Fri 19:15 IST** — price/technical refresh, re-rank on cached
  fundamentals. (Offset from the Smart Money Ledger's 19:00 cron.)
- **Saturday 09:30 IST** — full fundamentals refresh + framework re-score.
- GitHub cron can start 5–30 min late; that's normal.

## Failure behaviour (by design)

- NSE index CSV unreachable → last cached universe used, marked STALE in the
  provenance strip. Never silently wrong.
- A stock's yfinance fetch fails → excluded and counted, never guessed.
- Framework not computable for a stock (missing field) → its weight is
  redistributed across computable frameworks; card shows % coverage.

## Score audit trail

`data/score_history.json` logs every session's composite, grade, and price
per stock — the raw material for future out-of-sample "did A-grades actually
outperform" audits, mirroring the transition-evidence tracker in the Smart
Money Ledger.

## Disclaimer

Educational purposes only. Not investment advice. Not SEBI-registered
research. Data from NSE archives and Yahoo Finance; verify independently
before acting.
