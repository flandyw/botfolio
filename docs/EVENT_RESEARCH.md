# News and earnings research

This is a working conservative event pipeline, not evidence that news trading is profitable.
No real event profitability experiment has been performed or inferred from fixtures.

## Repository inspection and scope

Reused: `AlpacaClient` credentials/retries and paper-account checks, `load_history`/SQLite bar cache,
NYSE calendar (including early closes), `CostModel`, existing technical/random/buy-and-hold engine,
logging, Pydantic validation, Streamlit. Existing strategies and trading behaviour remain intact.
New modules live in `botfolio.events`; new event tables share the configured SQLite database without
replacing existing tables. No new core dependencies. `pip install -e '.[events-ml]'` enables LightGBM.

Reach: event models, providers, SQLite tables, AI adapters, research replay, optional ML, paper
executor, CLI, environment example, dashboard and tests. No secrets are printed or embedded.

## Data access, limitations and attribution

* SEC submissions and original filings are public without an API key. Set `SEC_USER_AGENT` to a
  real organization/contact email, and `SEC_COMPANIES` to e.g. `{"NVDA":"1045810"}`. Each provider
  uses <=4 requests/sec. Use one worker across the organization; SEC's overall limit is 10/sec.
  Complete submissions include exhibits, but this implementation skips documents over 200,000
  characters rather than silently truncating them. Polling covers recent submissions, not the
  SEC's older paginated archive files. XBRL current aggregates are never substituted for original filings.
* Alpaca news requires your existing market-data credentials and account entitlement. The provider
  covers symbol news (including earnings/guidance/regulatory headlines), paginates and archives
  revisions. Entitlement and completeness have NOT been verified against a real account here. This checkout
  contained no `.env`, Alpaca credentials, AI key or SEC contact configuration during validation.
  News arrives as generic `news`; import a sourced structured earnings snapshot for strategy A.
* Consensus estimates, historical analyst expectations and complete timestamped transcripts are
  not assumed free or available. Import legitimately obtained JSONL snapshots with provenance.
  Consensus requires a timestamp strictly before publication and a source. Missing values stay null.
  A transcript published later is a separate later event, never evidence available at the earnings release.
* SEC current filings can be collected forward for free. Alpaca documents historical news, but
  current/revised article text cannot be assumed to be the original text. Historical replay requires
  `snapshot_verified=true` on an independently verified content revision, using its `content_at`.
  This flag is an importer attestation, not an automated authenticity check. Preserve source archives.
* URL, publisher id, original publication, revision, actual receipt, full input, content hash, request,
  response, model, latency and optional configured input-token cost are retained. Original receipt
  survives deduplication. Changed text creates a new immutable revision.
* Current sector returns, consensus and related-event context are not fetched implicitly. Market
  context contains only completed prior bars; unavailable fields are explicit. No sentiment model
  is presented as calibrated market prediction. Optional FinBERT was omitted.

Sources: https://www.sec.gov/search-filings/edgar-application-programming-interfaces,
https://www.sec.gov/about/developer-resources,
https://docs.alpaca.markets/us/docs/historical-news-data,
https://docs.alpaca.markets/us/reference/news-3.

## Commands

Use the normal installation and `.env` setup described in README. Commands load the same
`--config`, `--db`, market-data credentials and logging configuration as existing commands.

```sh
botfolio events import snapshots.jsonl
botfolio events watch --experiment forward-v1 --once
# Continuous ingestion/analysis; paper execution remains disabled by default:
botfolio events watch --experiment forward-v1
botfolio events replay --start 2025-01-01T00:00:00Z --train-end 2025-06-30T23:59:59Z \
  --validation-end 2025-09-30T23:59:59Z --end 2026-01-01T00:00:00Z \
  --experiment frozen-v1 --run-id events-v1 --output event-research.json
botfolio events report
botfolio dashboard
```

`--cached` forbids historical downloads. The loader also needs seven days of context and four days
past the requested interval for the next session and exits. `events replay` also runs the existing
technical, random and buy-and-hold roster with $10,000 capital, .25% risk, 1% daily loss limit,
25% per-position exposure and one concurrent position. The reference run is linked in the report.
These continuous-exposure comparisons are not matched event experiments; do not compare raw returns
without considering their differing exposure. Existing technical-engine statistics retain their
existing assumptions; event reports make no significance verdict.

Minimal JSONL record (fixture only, never use synthetic content as empirical evidence):

```json
{"source":"licensed-archive","source_id":"example-1","ticker":"SPY","event_type":"earnings","published_at":"2025-01-06T14:30:00Z","content_at":"2025-01-06T14:30:00Z","received_at":"2026-01-10T12:00:00Z","source_url":"https://example.com/original","text":"Original timestamped release text.","mode":"historical","snapshot_verified":true}
```

## AI configuration

Default `EVENT_AI_PROVIDER=disabled` always produces WAIT. `chat` accepts a full HTTPS chat-completions
URL, model id and bearer key in `EVENT_AI_URL`, `EVENT_AI_MODEL`, `EVENT_AI_KEY`. Structured identity,
enums, null confidence and verbatim evidence are validated locally. Unsupported/malformed output,
timeouts and provider failures produce auditable WAIT decisions. No hidden fallback changes models.

`systemone` supports typed choice questions for Jev or Clef/Clef-flash; set the vendor's full
endpoint and model selector. For Cloudflare use your account's
`https://api.cloudflare.com/client/v4/accounts/ACCOUNT/ai/run/@cf/cloudflare/clef-flash`,
model `clef-flash` (or the corresponding `clef` route/model).
See https://developers.cloudflare.com/workers-ai/models/clef-flash/.
Jev requires your legitimate API access and its documented System One endpoint; no third-party
proxy or guessed vendor endpoint is selected automatically. Typed adapters cannot generate prose:
the archived full input is evidence and reasoning explicitly states this limitation. Vendor option
probabilities are retained only in the raw response, never treated as trading win probabilities.

An experiment id freezes provider/model/endpoint/prompt/schema and the first decision per event;
a changed configuration is rejected. Use a NEW id for every prompt/model/config
experiment; do not silently mix versions. Requests and responses are immutable. Input-token cost
is null unless a documented rate and token usage are available; output billing and network retry
charges are not estimated. No vendor calls were made during validation.

## Replay and scientific controls

Availability is revision time for verified historical snapshots and actual receipt/completion for
forward events. Entry waits at least max(measured inference latency, configured latency floor),
then uses a regular-session bar open; after-hours events wait for the next session. Historical
receipt is retained as the actual download time, never fabricated as a past bot observation.

Hold windows: 5/15/30/60 minutes and remaining session (390-minute upper bound), .5% stop,
no overnight positions, one open position per strategy/hold/split portfolio, <=25% exposure,
.25% equity risk sizing including a cost allowance, $100 daily realised-loss entry cutoff.
Stops can gap; risk sizing cannot guarantee a maximum realised loss. Each portfolio is independent.
Missing/zero-volume bars or suspected halts exclude the complete outcome path; exclusions may be
nonrandom and must be reported. Actual halt flags and historical NBBO quotes are not available:
spread/slippage are explicit configured assumptions, not measured executable quotes. Existing
cached bars may be adjusted and are feed-specific; corporate-action periods need separate audits.

AI high-materiality directional hypotheses must also contain evidence and a BUY/SELL recommendation
and must not have high uncertainty. Earnings requires known expectations surprise. Breaking news
filters event types. Technical confirmation requires aligned prior five-minute return and >=1.2x
volume. Inverse flips the admitted direction. All can abstain. Random is a paired counterfactual
with the same event, shares and exact exit time/reference as its parent strategy, not an independent
risk-managed portfolio. Its exit is conditioned on the parent stop; it answers a narrow direction
control question. One deterministic seeded draw is insufficient for a significance claim.

Train/validation/test boundaries are explicit; label windows crossing them are purged. Test data
is still displayed, so **do not use it for subsequent model selection**. Pre-register experiments
and hold back an additional untouched period if you inspect these outputs. Replaying historical
news through a modern model cannot rule out model-training contamination; forward collection with
a frozen model/prompt is the stronger experiment. No tool can recover an uncontaminated model from
an API that does not disclose its training data.

Reports retain every trade/control, exclusion and split; show net costs, win rate, profit factor,
closed-equity drawdown, dollar-minute exposure and day-clustered bootstrap mean daily P&L intervals
when >=5 traded days exist. Intervals exclude inactive days and are exploratory, not familywise
significance tests. Sharpe, Sortino, annualisation, exposure-normalised alpha and calibrated AI
accuracy are deliberately null/unavailable rather than misleading. Run cost and latency sensitivity
with new run ids (`--latency`, YAML cost config). Correct across ALL tried models, horizons, strategy
variants and sensitivity runs before a confirmatory claim; no automatic profitability claim is made.

## Optional ML

`botfolio events ml dataset.csv --output event-ml.json` uses one row per event, chronologically ordered
entry and exit, net_return as the explicitly defined after-cost outcome, and the features listed in
`botfolio.events.ml.FEATURES`. Dataset creation/provenance must be reviewed by the researcher; the command
does not infer earnings surprise from subsequent data. Missing features can be NaN. Expanding folds
split whole dates and purge training labels overlapping the next test period. Fixed small LightGBM
models report out-of-sample Brier score, accuracy, calibration bins and predictions. These are
research predictions, not wired to paper execution. Automated ML-only/AI+ML financial portfolios,
model selection/calibration fitting and full feature engineering are not implemented.

## Paper deployment

No orders are sent by default, and no service is installed or started automatically. For a dedicated
Alpaca PAPER account only, explicitly set both `EVENT_PAPER_ENABLED=true` and
`EVENT_EXCLUSIVE_ACCOUNT=true`. Do not share it with the technical mirror or manual orders: closing
brackets at deadlines cancels only orders belonging to the recorded intent. Broker paper endpoint/account guards execute
on every request. Only fresh forward long signals are eligible (five minutes after receipt in session, or through
the first five minutes of the next open for after-hours receipt); bearish hypotheses remain research
only (no leverage or short borrowing initially). Latest quotes must be <=10 seconds old with <=20bp
spread. Entry uses a bounded limit plus broker bracket stop. One position/reserved intent, 25%
exposure, .25% risk allowance and $100 daily account loss cutoff on the capped $10,000 experimental
capital apply outside AI. Cash and account blocked flags are checked.

Run one worker per database; Unix file lock prevents concurrent CLI workers. Durable deterministic
client ids reserve the intent BEFORE POST. Restarts reconcile broker orders rather than resubmit.
Ambiguous/missing broker orders block the cycle for manual reconciliation, sacrificing availability
for duplicate prevention. A crash between reservation and POST may require clearing the unsubmitted
intent after checking the broker; it never retries a guessed new id.

Create `data/EVENT_KILL` (or configured path) to block entries and trigger exit management. Time exits
and pre-close flattening require a healthy worker/API; bracket stops remain at broker, but disconnects,
halts and gaps can defeat expected fills or leave overnight exposure. Exit supervision runs independently of model work; supervise heartbeat and paper-account state externally. Broker lifecycle scenarios are tested with deterministic mocks; real fills and outage behaviour
remain to be observed during a regular paper-trading session. Forward news polls overlap one day, historical revisions deduplicate, provider
failures are logged, and pending fresh events are reconsidered after restart. Run under your existing
VPS supervisor with restarts and alerts only after broker sandbox failure testing.

SQLite WAL event tables are intentionally separate from engine snapshots. A PostgreSQL migration
would replace EventStore SQL and the SQLite transaction implementation (there is no automatic DSN
switch). Do not share a SQLite file across hosts. Raw source/model text in the dashboard is audit
data; do not publish the dashboard or redistribute licensed news without appropriate permissions.

## Validation and remaining evidence

`pytest` covers timestamp validation, immutable dedup, decision caching/evidence, latency, after-hours
execution, missing bars, split purging, absence of future context, cost-negative flat prices, matched
controls, disabled networking, paper endpoint safety and reservation before ambiguous submission.
These are deterministic fixtures, not historical performance evidence. Real SEC/Alpaca entitlements,
Jev/Clef wire responses, partial-fill/cancel races and operational outage recovery still require
sandbox integration validation before unattended use. No finding about empirical profitability is
supported yet: the current limitation is insufficient verified event data and forward observations.

### Credential/connectivity follow-up — 2026-11-17 UTC

After credentials were supplied, read-only checks verified an ACTIVE Alpaca paper account,
2,008 SPY IEX minute bars over the preceding seven-day query and five news responses containing
article content. A single watch cycle completed and wrote its heartbeat; its one-day QQQ/SPY news
query returned no events. A separate latest-news check archived and validated five QQQ/SPY revisions,
with five disabled-provider WAIT decisions and no submitted orders. The latest returned article was
2026-11-07, so API entitlement is verified but current/realtime news coverage is not established.
Do not treat this sparse/stale sample as a usable live news signal. AI-provider access, broker order
lifecycle tests and unattended operation remain unverified; paper execution remains disabled.

### Beginner dashboard

Start the existing dashboard with `botfolio dashboard`. **Start here** explains the setup state without
revealing credentials and offers read-only paper-account verification, collection of the last day's
news, and a local practice demo. The demo reuses the backtesting engine with generated prices and
$10,000 per strategy; its results are explicitly marked as educational, not empirical evidence.
**News & AI** shows source links, publication/receipt times, model reasons and explanations of WAIT.
Articles older than a day are labelled archived. **Results** translates the main financial measures
and offers CSV downloads; news experiments remain separate. **Help** explains the workflow and
terminology. **Advanced research** preserves detailed rankings, statistics, event audits and database
selection. No dashboard button starts a trading worker or directly submits a trade; Paper trading controls the separate worker’s entry and emergency-stop switches.
The collector timestamp is historical status, not a claim of active continuous monitoring. Refresh
the page after background work; changing environment settings requires restarting the dashboard.

For a temporary Cloudflare tunnel, set `BOTFOLIO_DASHBOARD_PASSWORD` to a strong, unique password
before starting Streamlit. The password screen blocks dashboard data and actions until sign-in;
Sign out clears that session's authentication. Local use without this setting stays unchanged.
Quick-tunnel links expire when cloudflared stops and do not provide a permanent hosted deployment.

### Clef activation — 2026-11-17 UTC

Cloudflare Clef returned a validated six-question decision through the live System One adapter in
1.34 seconds. The connection test used an archived article and returned WAIT; it is not a historical
trading result. The `clef-v1` analysis worker then saved five WAIT assessments for the existing
archived records and wrote a fresh heartbeat. Dashboard and tunnel health checks returned HTTP 200.
The dashboard was restarted with the new AI settings. This runtime explicitly disables event paper
orders, exclusive-account execution and the technical mirror; no order intents were created.
These processes were initially started manually; see the operational update below for the user services. Current news
coverage and profitability remain unverified.

### Operational paper-trading update — 2026-11-17

The Nasdaq calendar now gates entries to regular sessions; the broker clock must independently be
open and current. Holidays, early closes and daylight saving are respected. Entry requests set
`extended_hours=false`. New entries stop five minutes before session close; deadlines flatten two
minutes before close. After-hours news is eligible at the next opening only if originally received
within five minutes of that content revision. Downloading an old article never renews eligibility.
The configurable dashboard watchlist is shared with manual collection and the worker; changing it
does not stop management of existing positions.

Broker execution additionally requires `EVENT_PAPER_ENABLED=true`, `EVENT_EXCLUSIVE_ACCOUNT=true`
and the persistent `event-paper-armed` switch. The **Paper trading** page offers enable/pause,
emergency stop, collector status, executor health and the order journal. Emergency-stop clearing
leaves entries paused. Credentials remain on the server. Keep this account dedicated to the bot:
any other open account orders/positions block entries, and unexpected position quantities stop
reconciliation. No real-money endpoint is introduced.

Entry intents persist before POST; mutable requests are never automatically retried on ambiguous
network/server failure. Definitive 400/401/403/404 entry rejections close the intent; ambiguous
responses and missing reservations require operator reconciliation. Entries expire after 30 seconds
if not fully filled. Partial fills trigger cancellation of the remainder, followed by closing the
filled position after cancellation is confirmed. Bracket legs are retrieved explicitly using nested
order detail. Exits have deterministic ids and durable reservations too. Only this intent's entry
and bracket children are canceled; cancellation is confirmed on a subsequent poll before a market
exit is submitted. Closed/rejected partially filled parents are reconciled against actual positions.
The daily loss halt stays latched until the next New York date; restarting or a later equity recovery
does not reset it. Gap losses may exceed the risk allowance.

Exit supervision has its own asynchronous loop and bounded-timeout broker client, independent of
news/AI work. Entry and exit broker mutations are serialized. Heartbeats and failures are stored
for the dashboard. A worker/API outage, halt or cancellation rejection can still prevent scheduled
flattening; a broker stop is not an execution guarantee.

For this VPS, user-service definitions are in `deploy/botfolio-events.service` and
`deploy/botfolio-dashboard.service`. They use the persistent project `.venv`, the existing `.env`,
restart on failure and emit logs to the user journal. Install with:

```sh
mkdir -p ~/.config/systemd/user
cp deploy/botfolio-*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now botfolio-events botfolio-forward botfolio-dashboard
journalctl --user -u botfolio-events -n 50 --no-pager
```

Stop any earlier manually launched worker/dashboard first to avoid competing for the database lock
or port. This host has user lingering enabled, so enabled services start without an interactive login.
The existing Cloudflare quick tunnel remains a separate temporary process; a permanent public URL
and managed tunnel require a separately configured named tunnel/Access deployment.

Paper execution is an experiment, not evidence of profitability. No trade is forced just because
the market is open; stale news, missing context, weak assessments, risk gates or occupied accounts
can all produce no trades. Regular-session broker fill behaviour still requires forward observation.

Deployment validation: both user services were enabled and active with zero restarts, the protected
Cloudflare URL returned HTTP 200, and the production Python 3.12 interface test passed. The user
explicitly authorized actual Alpaca paper orders; the paper-entry switch was armed after confirming
an ACTIVE, unblocked, flat paper account with no open orders. The worker reported `market closed`
and created zero order intents outside regular hours. Next regular opening at validation time:
2026-11-17 09:30 America/New_York (14:30 UTC). The starting research watchlist is QQQ, SPY, AAPL,
MSFT and NVDA, editable in the control room; this is coverage selection, not a buy recommendation.
All automated tests isolate deployment credentials and block unmocked external requests.

### Tailscale-only dashboard

The unauthenticated instance is served through native Tailscale Serve at
`http://armvps.tail4832d.ts.net/` (or `http://armvps/` with MagicDNS).
`botfolio-dashboard-tailscale.service` binds Streamlit to loopback `127.0.0.1:8502`; Tailscale Serve
proxies tailnet port 80 to it. It is not a Funnel/public route. Setup on this host:

```sh
sudo tailscale serve --bg --http=80 --yes http://127.0.0.1:8502
```

The service bypasses the application password only for that process via an empty
`BOTFOLIO_DASHBOARD_PASSWORD`; the shared `.env` and password-protected Cloudflare instance on
loopback port 8501 are unchanged. Tailnet access rules control which devices can reach the app;
allowed peers can use its paper controls without another login. The earlier direct Tailscale port
8502 URL is retired. Local origin health and the WebSocket hostname origin were verified; remote
access must still be confirmed from the user's device. HTTPS Serve currently requires enabling
HTTPS for the tailnet; HTTP Serve works without that change and travels inside Tailscale's encrypted
network connection.
