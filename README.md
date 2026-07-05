# Elite Stock Screener — Midcap 150 · Smallcap 250 · Multibagger Radar

Automated, rule-based quality screener for Indian mid/small/micro-cap equities.
Every stock in the Nifty Midcap 150 + Smallcap 250 (+ Microcap 250, radar-only)
universe is scored against
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

**Multibagger Radar** hunts in Smallcap 250 + **Microcap 250** (microcaps never
appear in the Primary Screener or Queue - they are the multibagger hunting
ground AND the manufactured-volume hunting ground, hence the hard integrity
gates). Gates: mcap ≤ ₹25,000 cr, 3y revenue CAGR ≥ 15%, 3y PAT CAGR ≥ 18%,
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

## v1.1 — the nine institutional gaps, closed or honestly named

Implemented from a structured PM critique of v1.0:

1. **Attribution loop** — every run logs composite/grade/price/framework scores;
   after 21+ sessions the dashboard publishes median forward return *by grade
   band* (1m/3m/6m) and a Spearman rank IC per framework. Persistent near-zero
   IC for a framework is published evidence its weight is decorative.
2. **Weight validation** — same mechanism: weights stay as priors until the IC
   table earns or indicts them. No fake backtest is pretended (point-in-time
   fundamentals aren't freely available; forward evidence is collected instead).
3. **Portfolio layer** — per-tab sector concentration (warn > 30% one theme:
   "17 tickers, one macro bet"), pairwise 6m return-correlation flags among
   top names.
4. **Sell discipline** — watch-state engine records entry composite/grade when
   a name enters any tab; composite decay ≥ 1.5, a 2-band grade fall, or a
   fresh veto raises a persistent **EXIT REVIEW** banner. Entry is no longer
   the only door.
5. **Volume-integrity gates** — minimum 200 sessions of history, top-3-day
   volume share ≤ 60% of the 20d total, zero tolerance for zero-volume days.
   Flags shown everywhere; **hard gates** on the Multibagger Radar, which is
   the most manipulable surface by design.
6. **V6/V7 manual vetoes** — related-party transactions and auditor
   change/resignation added to the checklist. Not automatable from free
   structured data; never silently passed.
7. **Regime overlay** — Pabrai/Damani valuation components score PE *relative
   to the live universe median*, not absolute thresholds. "Expensive" is
   regime-dependent; the median is printed on every run.
8. **Structural bear case** — a deterministic devil's-advocate extracts the
   most damaging true statements from each stock's own data (weakest
   framework, valuation percentile, extension, CFO decline, coverage gaps,
   volume flags) onto every card. Skepticism as a system property, not a habit.
9. **Position sizing** — inverse-volatility suggested weights capped at 8%
   per name and 30% per theme. A risk-budget illustration, never advice.

## v1.4 - six loopholes closed (institutional review round 2)

1. **Split safety**: all price history is split/bonus-adjusted (`auto_adjust`);
   attribution computes BOTH return endpoints from the same adjusted series, so
   corporate actions can no longer fabricate crashes in the evidence.
2. **Survivorship**: symbols are followed after leaving the universe (ghost
   fetch); delisted names contribute their terminal return; names with no
   retrievable history are counted and published with an explicit upward-bias
   warning - failures can no longer vanish from the grade-band table.
3. **Degraded-run guard**: if <70% of the universe has price history, or the
   scored count collapses vs the last published run, the pipeline refuses to
   publish and touches nothing (tabs, watch-state, score log). A rate-limit
   storm is a data failure, not a market event. Override: `--force-publish`.
4. **Coverage penalty**: composite loses 2.0 x missing-coverage share -
   opacity now costs points instead of hiding risk. Radar requires >= 85%.
5. **Promoter demoted**: yfinance's insider field is not the SEBI promoter
   category; it no longer scores (Kedia) or gates (radar) anything -
   display-only with a verify-NSE-SHP flag until a proper source is wired.
6. **Pre-run enforced**: radar rejects names > 35% above their 200 DMA or
   > 80% up in 6 months (momentum confirmation, not discovery), and tracks
   signal age per name - stale after 30 sessions on the radar.

## v1.5 - review round 2 complete + point-in-time backtesting

- **#7 Divergence drift**: ACE-vs-mechanical gap is baselined per name at
  first sight; the re-review alarm fires on DRIFT >= 1.0 from that baseline,
  not on the structural ACE premium. An alarm that is always red is not an alarm.
- **#8 Earnings quality**: Jhunjhunwala damps PAT spikes that wildly outrun
  revenue (exceptional-item signature) and caps momentum when CFO is declining
  - paper earnings without cash are not momentum.
- **#9 Board names never go silent**: leaderboard symbols are force-scored
  every run even outside the index/liquidity screen; an unscoreable board name
  raises the loudest exit-review alarm instead of disappearing.
- **#10 Regime context**: Nifty 200DMA state, 3m trend, and India VIX join the
  universe-median-PE line (best-effort, never blocks a run).

### Backtesting (`backtest.yml` workflow)
Runs the SAME engine as-of a past date with point-in-time enforcement:
prices truncated at the date; only statements ended 90+ days prior are
"known"; ratios recomputed from dated filings; unreconstructable fields
(beta/dividend/promoter) degrade with the standard coverage penalty. Forward
returns come from the same split-adjusted series. Every output carries a
`caveats` block - notably UNIVERSE SURVIVORSHIP (today's constituents; free
sources do not provide historical index membership), so trust the A-vs-C
grade SEPARATION and ICs, not absolute return levels. Sources: NSE archives
(constituents) + Yahoo Finance dated statements/adjusted prices. Screener.in
is a display/export product without free point-in-time snapshots - a paid
CSV pipeline can replace the fundamentals leg later without touching scoring.

Run: Actions -> "Point-in-time backtest" -> asof (e.g. 2025-10-01), horizons "63,126".

## v1.6 - Deep-Dive automation (mechanical prep, not judgment)

Two additions, deliberately scoped to NOT automate ACE judgment itself -
that qualitative layer is the entire reason ACE scores richer than the
mechanical composite, and auto-filling it would collapse the
divergence-drift alarm's whole purpose.

1. **Auto-drafted ACE worksheet** (`worksheet.py`): every Deep-Dive Queue
   candidate gets an 11-section worksheet mirroring the Elite Stock Analysis
   Engine v2.0 methodology, each section tagged AUTO or MANUAL:
   - AUTO: company overview, financial snapshot, 8-framework scorecard,
     technical structure, sector-peer comparison, auto-generated bear case,
     automated V3 veto status.
   - MANUAL: the 6 mandatory web searches (exact query strings generated,
     never fake results - GitHub Actions has no search credentials and a
     fabricated finding is worse than an honest gap), qualitative judgment
     (moat/management/governance), trade plan, final verdict.
   Turns a ~45-minute research task into a ~10-minute review-and-decide task
   with zero loss of your judgment in the actual score.

2. **Deep-Dive Queue digest** (`digest.py` + a GitHub Issue upsert step in
   `screener.yml`): each run diffs the queue against last run's membership
   and posts what's NEW (not the full list every time, to avoid notification
   fatigue) to a pinned GitHub Issue titled "Deep-Dive Queue digest" - using
   only the workflow's built-in token, no WhatsApp/email API keys to
   provision. GitHub's own notification system (mobile app, email) then
   delivers it to you like any other issue update.

## v1.7 - Section 8 upgraded: grounded, cited research (server-side only)

**Why this is NOT an API-key field in the HTML:** the dashboard is a public
GitHub Pages site with no backend. Any client-side key would sit in plain
JavaScript, visible to every visitor via browser dev tools - a real leak of
your Anthropic billing key to the entire internet, not a theoretical risk.

**What was built instead:** `research.py` runs inside the GitHub Actions
pipeline (server-side) and calls the Claude API with the web_search tool for
Lane 1 candidates only (capped at 5/run for cost control), compiling the same
6 mandatory-search topics into cited findings. Uses a GitHub encrypted
secret - never a file, never this chat, never the public site.

**Setup (do this yourself, not in chat):**
1. Get a key at console.anthropic.com (separate API billing from claude.ai)
2. Repo -> Settings -> Secrets and variables -> Actions -> New repository
   secret -> name `ANTHROPIC_API_KEY`, paste the key there

**Failure behavior, by design:** no key set -> Section 8 shows the manual
query list only (unchanged from before, explicitly marked "not configured").
API call fails or returns unparseable output -> falls back to the manual
list with an "unavailable this run" note. NEVER fabricates a finding -
verified in testing that a bad/garbage model response is discarded rather
than surfaced as if it were real research.

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
