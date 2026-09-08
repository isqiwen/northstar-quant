# Northstar Quant

A personal domestic-futures trading system targeting controlled live execution.
One repository, one Python package, PostgreSQL and managed private data files. The delivery path is
read-only broker access → broker simulation → recovery and reconciliation → a
bounded, explicitly authorized live round trip; advanced research comes later.

Currently implemented: retain original uploaded bytes, inspect processing failures
and publication, import minute bars, replay a strategy with Risk and trading
costs, save fixed configurations, and advance a recoverable file-driven Paper
account in your browser. A concrete CTP adapter now provides explicit bounded
SimNow read-only queries and saved browser evidence. On 2026-09-05, the approved
`simnow_dev` account passed actual TD authentication/login, all seven TD queries
returned terminal replies, and the selected contract subscription produced one market observation.
That bounded-query evidence is not a continuous or freshness-verified feed; see the
[acceptance evidence and remaining gaps](docs/broker-source.md).
The current #25 slice adds bounded durable TD/MD reception and account-neutral
minute shadow signals, plus explicit local archive/processing of a saved callback
prefix into a reusable market segment. Fresh continuous external-market acceptance
remains incomplete; archiving old records does not provide that evidence.
Broker orders and live execution are not implemented. Research, Paper and successful queries never enable real
orders; see the [architecture and live gates](docs/ARCHITECTURE.md#9-首个受限实盘闭环).

## Selected architecture and current deployment

The target is three independently deployable applications, each with its own Web,
configuration, startup/upgrade/shutdown and diagnostics:

| Application | Location | Owns |
|---|---|---|
| [Northstar Data Hub · 数据管理中心 #41](https://github.com/isqiwen/northstar-quant/issues/41) | Local `core`; `apps/data_hub/` | Collection, processing, quality, snapshots, query/export and Data Hub Web |
| [Northstar Research · 量化研究工作台 #23](https://github.com/isqiwen/northstar-quant/issues/23) | Workstation; `apps/research/` | Research Web, durable tasks, independently supervised execution and results |
| [Northstar Live · 实盘交易系统 #40](https://github.com/isqiwen/northstar-quant/issues/40) | Domestic cloud; `apps/live/` | Live Web and a separately supervised trading kernel with local authoritative storage |

Application paths are relative to `src/northstar_quant/`. There is no Console application.
The `data_management/` business Module is distinct from Data Hub composition.
Shared UI components do not share business
authority or task ownership. Applications may contain multiple processes.
Research uses available CPUs and supported GPUs subject to measured memory/disk
budgets and responsive management/cancellation, not a fixed core count.

**Current implementation:** three independent Web entrypoints and an independently
supervised Live kernel. The central Console factory and `serve` command are removed.
Live Web uses authenticated kernel HTTP without opening a database or source directory.
Data Hub owns ingestion routes; Research owns research/configuration/Paper routes.
Local Data Hub and Research currently share fixed local data and PostgreSQL;
independent durable execution, continuous collectors and cross-host snapshot
delivery remain #23/#41/#42, not completed by this entrypoint split.

[The first split, #39](https://github.com/isqiwen/northstar-quant/issues/39), separates
process-isolation evidence from actual SimNow continuous-feed acceptance, without
granting order-sending authority. The
[cloud runtime foundation, #40](https://github.com/isqiwen/northstar-quant/issues/40),
follows without waiting for order execution or the completed funds ledger; it
does not itself enable trading or prove recovery. Live will
keep real-time inputs and authoritative trading records close to the broker
connection, independent of local Data/Research availability. Data receives
fixed archives asynchronously; it does not become a second writable account ledger.
Research consumes fixed published data, and Live activates only explicitly selected,
verified configurations/artifacts. Web disconnection never grants unattended
execution; existing authorization and supervision conditions still apply.

Local Data/Research may share a PostgreSQL instance with separate record ownership.
Cloud Live has its own nearby durable storage, not a public connection to the local
database. Only Live receives broker credentials. Process separation does not create
three repositories, duplicate Strategy/Risk/Accounting implementations, or introduce
generic contracts, a message bus or a compatibility layer. See the
[role and ownership design](docs/ARCHITECTURE.md#1-设计判断与交付边界) and
[development order](docs/ROADMAP.md); [Project 1](https://github.com/users/isqiwen/projects/1)
tracks implementation and acceptance rather than treating this design as completion.

## Run

```sh
docker compose up --build -d
```

Open Live at <http://127.0.0.1:18080>, Data Hub at <http://127.0.0.1:18082>,
and Research at <http://127.0.0.1:18083>.
Run individually with `northstar live-web`, `northstar data-hub`, or
`northstar research-web`; `northstar live` starts only the trading kernel. Live Web, Live HTTP and PostgreSQL publish only on
local loopback; this is a same-host deployment, not a public remote-control server.
Initialization creates private, separate read/control tokens in the runtime-auth
volume, without printing them. Those tokens are not broker credentials.
`docker compose restart live-web` restarts only the workspace; `docker compose down`
stops all roles and keeps persistent volumes. Restarting Live never reconnects or
resumes old reception. Do not run multiple Live workers or Uvicorn auto-reload.
Compose persists database, managed source and backup volumes separately; retain
database records and their referenced files together. Container rebuilds do not
discard sources. Neither uploaded market files nor backups belong in public Git.
Initialization accepts only the current data baseline; it never resets an
existing database. After a backup, `northstar init-db` can add a Module's new
tables when existing fact shapes are unchanged. A replacement of existing storage
shapes requires explicitly preserving needed evidence and preparing the current
storage, not an in-place legacy compatibility path.

Data Hub imports a bounded CSV with explicit source, contract and session
metadata. Select accepted data directly from the library, including after a
restart, inspect its source and quality, then research without re-uploading the
file. Reports show the saved equity curve, fills, costs, holdings and risk
decisions alongside fixed input evidence. Empty state contains no invented results.

### Current local container limits (not production sizing)

The current Compose deployment applies these per-container ceilings:

| Role | CPU quota | Memory (no additional swap) | Processes/threads |
|---|---:|---:|---:|
| Live | 2 CPUs | 1 GiB | 128 |
| Live Web | 1 CPU | 1 GiB | 128 |
| Data Hub / Research | No fixed CPU/memory quota | Budget-aware execution remains #41/#23 | — |
| PostgreSQL | 1 CPU | 1 GiB | 128 |
| One-shot initialization | 1 CPU | 512 MiB | 64 |

The bounded limits above belong to the local Live installation profile, not the selected Research resource
policy or production Live sizing. Change these limits only through controlled replacement and measured capacity checks.
Application-specific deployment work in #40/#23/#41 must replace arbitrary default
quotas with measured protection. CI may retain a deliberately bounded test profile.
These limits are neither reserved resources nor demonstrated peak-load capacity. Host memory must also cover Docker, the OS and other workloads. A memory
limit can cause OOM termination; CPU throttling can make quotes stale. Neither
failure permits automatic broker reconnection or inherited execution authority.
Do not disable OOM protection. Tune explicit limits against measured workloads
before cloud acceptance; native processes outside Compose are not constrained here.

All containers use Docker's `local` log driver with rotation at 10 MB and
three retained files per container. Read logs through `docker compose logs`; rotated
process logs are disposable diagnostics, **not the durable account/callback ledger**.
Rotation is not a volume or database disk quota, and does not replace capacity
monitoring or backups. Existing containers need controlled recreation to apply these
settings; `restart` alone does not apply changed resource/log configuration.
See [Docker resource limits](https://docs.docker.com/engine/containers/resource_constraints/)
and [local log rotation](https://docs.docker.com/engine/logging/drivers/local/).

Before recreating containers, stop bounded reception explicitly, confirm it has
ended, and complete a joint backup. Then use the same Compose files used to start
the deployment (including `compose.simnow.yaml` when configured). Verify the applied
limits with `docker inspect`, service health, and that saved streams remain unattached;
never use `down -v`. These local controls do not complete #40's cloud isolation,
independent alerting or disk-capacity acceptance.

### Web interface

Each application uses FastAPI and NiceGUI 3.16.0, with one NiceGUI mount per
process. All three home pages and the Live continuous-detail page use native
library controls; shared HTTP/security/presentation in `web/` is not an application.
The obsolete combined import/research HTML and corresponding JavaScript are removed.
Existing source, research-result, Paper and broker detail views still use their
current HTML rendering, now routed by their owning application. This is not a claim
that every detail view is already converted. New pages should use NiceGUI/Quasar
and library charts, with only small justified custom HTML/JS/CSS.

Each app uses a distinct browser session cookie. Live credentials authorize kernel
queries and fixed commands, not broker execution. Data Hub uploads are bound to
their originating browser page and have a server-side request-size limit.
The current entrypoints remain loopback-only; protected remote deployment is #40.

Use `127.0.0.1` or `localhost` directly, without proxy forwarding. Connected UI
events are bound to the page's short-lived browser session and same origin;
HTTP commands retain CSRF checks. A disconnected detail page disables further
actions: reopen it rather than replaying offline clicks. Polling never changes
the selected evidence, saved-prefix bounds, UTC range or decimal price. The
NiceGUI document has a narrowly scoped runtime CSP exception; other pages keep
the default policy. See the [interface and security design](docs/ARCHITECTURE.md#各应用自有-web按实际行为逐步交付).

## Live runtime diagnostics

Open `/live` or run `northstar live-check` using the existing Live deployment
authentication. This read-only observation checks PostgreSQL reachability and
the **Live source filesystem's** available bytes/inodes, without scanning files,
writing probes, repairing storage or connecting a broker. The CLI exits 2 on
degradation or failure to obtain a current authenticated response; otherwise 0.
The page is a timestamped observation, refreshed explicitly, not a live alarm.

`OK` applies only to these checks. PostgreSQL filesystem capacity, archive quota
usage, write durability, market freshness and account reconciliation are not
established. In particular the database may reside on another filesystem: its
disk capacity remains `UNKNOWN`, never inferred from the source directory.
Missing source storage is reported without recreating it. Low available space
uses the existing `NORTHSTAR_ARCHIVE_MIN_FREE_BYTES` threshold; it does not grant
execution authority or add an automatic receiver shutdown policy.
This is an engineering slice of #40, not cloud deployment or independently
delivered whole-host alerts. `/health/ready` retains its database-only meaning.

## SimNow connection

Live requires **Linux amd64** with `ctpwrapper==6.7.13`. The local Compose example
uses that image for the current same-host development services, including on Apple Silicon. Live Web can instead
run natively on macOS/arm64 and use Live HTTP; it does not load the native SDK.
The [SDK evidence and limitations](docs/broker-source.md) separate offline native
verification from actual authentication and account-query acceptance.

In your own terminal, run the private configuration wizard:

```sh
bash scripts/setup_simnow.sh
```

It saves literal values in owner-only `.northstar/simnow.env`, excluded from Git
and Docker build context. Do not source this file, paste its contents into chat,
or add credentials to HTTP requests. The wizard does not connect or trade.
For a database already on the current baseline, attach that file only to Live:

```sh
docker compose -f compose.yaml -f compose.simnow.yaml up --build -d
docker compose exec live northstar broker-sdk-check
```

`broker-sdk-check` constructs the actual query structures with synthetic identifiers,
creates native handles and configures report topics using the same calls as a query,
then releases them without initializing a connection or sending any request.
Open `/broker`, explicitly select `simnow_dev` or `simnow_trading` and a concrete
futures contract, then click “连接并查询（只读）”. Only these two operator-approved
environments are accepted. Profile definitions live beside the adapter, not in
the credentials file. No connection starts on page load, restart or restore.

The query records CNY funds and whole-account positions/orders/trades, plus the
selected contract, margin/commission terms and a bounded market observation.
The instrument query can return same-prefix options: retain every raw callback,
but select the requested futures contract by exact identity. MD login is separate
from TD account authentication; absent MD identity fields remain unknown.
Missing replies differ from confirmed empty results. `COMPLETE` means reply
collection completed, **not** a reconciled account; local broker-ledger differences
remain unknown until that ledger is established. No order, cancel, settlement
confirmation, transfer or password-update operation is exposed in this stage.
Controlled simulation orders/cancels are the next execution slice, not prohibited
permanently.

The same entrypoints are available from the installed CLI:

```sh
northstar broker-status
northstar broker-query simnow_dev --instrument rb2610 --request-id REQUEST_UUID
northstar broker-list
northstar broker-show REQUEST_UUID
```

For a native Linux amd64 Live installation, set `NORTHSTAR_SIMNOW_CONFIG` only
in Live's environment to the absolute private file path. Live Web and broker CLI
commands use `NORTHSTAR_LIVE_URL` and `NORTHSTAR_LIVE_AUTH`, not broker secrets.
Reusing a request UUID retrieves its fixed receipt and query,
including a failed or interrupted one; a deliberate new query needs a new UUID.
Only one query per environment/account runs at a time. A native crash or timeout
is confined to a short-lived child inside Live. Saved final evidence survives restart; an interrupted parent leaves
`PENDING`, not a claimed complete or continuously journaled capture.

### Fixed account observations

On a saved query page, “固定本次观察为基准” records a complete, flat CNY observation
locally. All positions/orders/trades must have completed empty responses, and
observed margin, freezes and position profit must be zero. Missing funds are not
zero. This is an observation baseline, **not an external-fill ledger**.

After fixing it, explicitly request another read-only query and compare that saved
result on its page. The later query must belong to the same environment/account,
start after baseline creation, and not reuse the source request. One environment/account
has one immutable baseline; later balances never overwrite it to erase a difference.
These local buttons need no credentials and never connect to the broker.

```sh
northstar broker-baseline SOURCE_QUERY_UUID --request-id BASELINE_UUID
northstar broker-baseline-context SOURCE_QUERY_UUID
northstar broker-compare BASELINE_UUID LATER_QUERY_UUID --request-id COMPARISON_UUID
```

Comparisons retain exact observed monetary differences and whole-account activity,
including other contracts. `MATCHED` means no observed change in this limited scope;
`DIFFERENCES` is unexplained change, not attributed P&L; `UNKNOWN` includes incomplete
fields or a changed trading day requiring settlement facts. Every outcome remains
`UNRECONCILED`, without trading authority or a claim of continuous coverage.
Original query records and comparison records remain separate and immutable.

For an existing current-data database, back it up with the running version before
deploying this slice; initialize the two new baseline tables with the new version's
`northstar init-db`. Keep the same database and source storage; do not drop or reset them.

### Confirmed trades and gross positions

The saved-query page can append its confirmed trade callbacks to a position ledger
rooted in the fixed flat observation. Repeated callbacks and later query repeats
do not increase holdings twice; conflicting trade identities retain the original
fact and block a known projection. Buy-open and sell-open remain separate long
and short holdings. Confirmed quantities do not imply known fees: this ledger
never substitutes zero fees or the research account for a broker cash ledger.

The current scope is one trading day, SHFE futures and speculation, with explicit
open/close-today/close-yesterday effects. A later open cannot hide an earlier
close without established holdings. Unsupported effects, unmapped contracts,
incomplete queries and missing previously recorded trades remain visible as
`UNKNOWN`. No correction or automatic reset operation exists in this slice.
Each day is bounded to 1,000 append entries and 10,000 distinct fills.

Contract UUIDs come from Data's existing catalog. An exact Instrument response
can register a contract under an already registered product, but cannot invent
the physical quantity unit missing from that response. Missing product metadata
or conflicting terms must be resolved through Data before usable trade projection.
Original callback evidence remains readable; no CSV, calendar or Snapshot is
fabricated to register a broker fill.

```sh
northstar broker-ingest BASELINE_UUID SOURCE_QUERY_UUID --request-id ENTRY_UUID
northstar broker-ledger SOURCE_QUERY_UUID
# Explicitly obtain a new authorized read-only query after fixing the entry.
northstar broker-positions ENTRY_UUID LATER_QUERY_UUID --request-id CHECK_UUID
```

The independent check compares gross today/yesterday quantities and lists later
unrecorded trades without importing them into its fixed expected ledger. It also
retains the full observed order/position evidence. `MATCHED` here means only
position quantities agree; fees, cash flows, settlement, order-state reconciliation
and continuous event coverage are not established. Every result remains
`UNRECONCILED` without order or cancel authority. Repeated commands, restart and
restore preserve fixed results; integrity checks never repair missing catalog facts.
Back up the running database before deployment, then explicitly run `init-db`
to add the two position-ledger tables without replacing retained account records.

### Order observations and recorded fills

On a saved independent position comparison, “核对委托与已入账成交” fixes an
order review using that comparison's exact historical ledger and later query:

```sh
northstar broker-orders POSITION_CHECK_UUID --request-id ORDER_CHECK_UUID
```

It shows reported order and submission states separately, cumulative traded
quantity, linked individual ledger fills and their signed difference. Unrecorded
trades from the comparison query remain differences; they never fill their own
ledger gap. Later ledger entries cannot change an earlier review's inputs.
Repeated observations do not duplicate fills; conflicting identities, declining
cumulative quantities, changed terminal states and missing previously observed
orders remain unknown. Empty exchange order IDs retain their original fields
without guessing an association from OrderRef alone. At most 10,000 observations
are reviewed per command.

Cancel-submitted and cancel-rejected are not cancellation completion. A canceled
order can still lack recorded fills; its unfilled quantity is not necessarily
queued quantity. This is an external order observation, not a locally sent or
owned order, continuous lifecycle recovery or permission to release reservations.
`MATCHED` only describes this observation/fill scope; all results remain
`UNRECONCILED`, with no sending authority. These commands read no broker credentials and do not connect.
After backing up the running database, `init-db` adds the single order-review
table; original queries, position entries and comparisons remain unchanged.

### Bounded reception and shadow signals

`/streams` starts an explicit SimNow TD/MD connection from an existing `COMPLETE`,
identity-confirmed query, canonical SHFE product/contract metadata and an immutable
configuration revision. Select the saved query and configuration, set 60–7200
seconds, and explicitly declare permitted local retention and its usage basis.
The old query fixes the connection scope; it is not current account or price evidence.
Only one receiver per application database runs; same-account bounded queries
cannot overlap it. Page load, refresh, restart and command retries do not reconnect.

This is `SHADOW_ONLY`: Strategy emits account-neutral targets, without Account
Risk, simulated fills, cash, orders or cancellations. Configuration cash, costs
and margin assumptions do not become broker facts. `PAUSE` stops shadow calculation
but continues retaining callbacks; `RESUME` resets warmup rather than replaying
missed decisions; `STOP` ends this connection, not an order or position.
`STOP_REQUESTED` is not confirmation that reception has ended. A stopped or
interrupted receiver cannot be resumed into another connection.

The source is `COPIED_CTP_CALLBACKS_POSTGRESQL`: immutable, sequenced SDK whitelist
callbacks in PostgreSQL, committed before their corresponding processing step.
Each stream is bounded to 100,000 events and 128 MiB. This is neither vendor wire
bytes nor itself a published research Snapshot. Explicit prefix archiving now
uses Data's existing source/attempt/publication path, described below; #25 remains
partial. Actual fresh continuous-market acceptance has not yet been performed for this slice.

The first scope is the [SHFE DAY continuous sessions](https://www.shfe.cn/services/calenderandholidays/tradinghours/):
09:00–10:15, 10:30–11:30 and 13:30–15:00, Asia/Shanghai, with explicit source dates
and one confirmed trading day. Night trading and auctions are unsupported.
The initial minute and the first minute after a break are partial; the last minute
is not flushed on a timer or shutdown. Later accepted observations confirm completion.
OHLC describes observed LastPrice samples, not a reconstructed trade tape; volume
is a cumulative difference assigned to the arriving snapshot's time, not proof of
exact per-minute exchange volume. Source/receipt freshness and intra-session gaps
have a five-second engineering limit; unknown or conflicting time/volume stops
shadow calculation. The limit is not an exchange guarantee or a fallback to zero.
Only a trusted preceding session-end observation permits a scheduled-break label;
otherwise silence remains stale/unknown, not assumed healthy market closure.

The report shows persisted/processed sequence, source and receipt times, reasons,
the latest ten minute/signal results and their callback evidence. The NiceGUI page's
one-second timer reads the owning Live's state, rechecking the bound browser session;
it stops on read failure, disconnect or a terminal stream state.
An available receiver or successful subscription does not establish market freshness
or account reconciliation.

```sh
northstar stream-list
northstar stream-show STREAM_UUID
northstar stream-events STREAM_UUID --after 0
# Explicit connection in Live; this CLI command returns without stopping reception.
northstar stream-start QUERY_UUID --configuration CONFIGURATION_ID --seconds 300 \
  --allow-retention --use-basis 'YOUR_CONFIRMED_LOCAL_USE_AND_RETENTION_BASIS' \
  --request-id STREAM_UUID
northstar stream-control STREAM_UUID PAUSE --request-id CONTROL_UUID
northstar live-command-show CONTROL_UUID
```

The CLI needs Live deployment authentication, never broker credentials. A new start
requires the verified Linux amd64 SDK and matching private account configuration
in Live. Web and CLI observe/control the same owner. Commands bind their runtime,
input, UUID and short expiry; uncertain outcomes are queried by the same UUID, not
blindly resubmitted with new identities. A changed owner invalidates old controls.
Reusing the start UUID cannot open a second connection. Before deployment, back up with the running
version, then run the new version's `northstar init-db` to add the four stream
tables and apply the current source-kind constraint; preserve the existing database
and source directory. Restored stream
source/step chains are verified, never automatically attached or reconnected.

### Archive saved callbacks for research or Paper

On a stream's detail page, explicitly choose the saved prefix `1..through_sequence`
and a complete, minute-aligned UTC range `[session_open, session_close)` within one
supported SHFE DAY interval. Polling never changes those form values. This action
reads local records only: it does not start a connection, resume shadow calculation
or issue orders. The entire fixed JSON prefix must fit within 5 MiB; an oversized
prefix is rejected, never silently truncated to fit.

Data retains it as `CTP_CALLBACK_SEGMENT`, with its original callback sequence,
receipt times, binding and hashes. The JSON contains TD account information as well
as market callbacks. Retention is required by the original stream declaration;
local download is a separate permission, **off by default**. Do not publish the file
or treat local download permission as permission to redistribute it.

```sh
# Explicit local processing of saved evidence, with your selected UTC range.
northstar stream-archive STREAM_UUID --through-sequence SEQUENCE \
  --session-open START_UTC --session-close END_UTC --request-id REQUEST_UUID
```

The result is a Data processing attempt. Follow it at `/attempts/ATTEMPT_UUID` or
from the stream's archive list; failures retain their source and explanation.
Range correction on the attempt page processes the same bytes without changing
its stream identity or prefix. Request UUID retries preserve the original outcome.
Only a quality-accepted publication becomes selectable for research or file Paper;
source, attempt, Snapshot and subsequent usages link back to the saved stream.

The availability basis is `LOCAL_CAPTURE_RECONSTRUCTED`: minutes are recomputed
from the original local receipt clock, not the archive date or the latest shadow
steps. This is not a replay of the decisions, pauses or processing delays in the
original shadow session. Missing/partial/unconfirmed minutes are not filled in;
LastPrice OHLC and cumulative-volume deltas remain sampled observations, not a
vendor trade tape, exact exchange OHLCV or production-price evidence. A series
cannot mix another fixed source or different sampling semantics, even when prices
match. This source meaning cannot be declared through the CSV upload form.

The slice reuses the current Data library, source/attempt tables, managed files,
Snapshot and research/Paper interfaces, without another storage service. Missing
or corrupt evidence blocks new use; saved results remain inspectable with the
source's condition. Joint backup/restore includes these references and files.
Implementing and exercising this local path does not verify a fresh external feed;
it adds no broker connection, query or order, and does not complete #25.

### Budget one opening lot from a saved shadow target

On the stream page, select a saved target, an existing same-account order review,
and a decimal limit price. Direction and risk limits come from the original target
and stream configuration, not a manually supplied account or a newer template.

```sh
northstar broker-opening-budget STREAM_UUID --sequence SEQUENCE \
  --order-check ORDER_CHECK_UUID --limit-price 3110 --request-id REQUEST_UUID
northstar broker-opening-budget-show REQUEST_UUID
```

The immutable result links back to the exact shadow step, independent position/order
review, account query, rates and original times. It is a **non-executable budget**:
`WITHIN_BUDGET` means only that one lot fits the numerical constraints of those saved
inputs. It creates no order, simulated fill or cash reservation, and never replays
the original decision. Unknown facts remain `UNKNOWN`; insufficient funds or limits
produce `REJECT`. Creation returns CLI exit code 0 only for `WITHIN_BUDGET`, otherwise 2.

The first slice requires a same-day, flat CNY SHFE speculation account, no orders or
trades, zero margin/freezes, explicit futures business and absolute account-specific
rates. Missing business/investment-unit fields in earlier evidence are not inferred.
Money- and lot-based rates are additive; simulated cash, margin and fees are excluded.
BUY limit / SELL daily upper limit bound notional and fee budgets; margin separately
uses the higher of the observed daily upper limit and previous settlement price.
Budgets round upward to cents, not to alleged actual charges. See [the source and
assumption notes](docs/broker-source.md#单手-shfe-投机开仓预算依据).

Source time, receipt time, query window and calculation time stay separate. Old
observations do not become current through this operation. Execution blockers remain
visible even when numerical budgeting succeeds: account-event reconciliation, actual
cash/fee accounting, reservations and authorized sending are not yet established.

### Automatic saved-reply booking and account money

A new receiver binds the account's existing flat baseline before connecting.
Each callback commits first, then the account applies saved asynchronous replies,
then shadow calculation may proceed. Pausing shadow does not stop trade booking.
Without a baseline the receiver remains explicitly unbound and shadow-only; it
cannot claim an account position. A fixed baseline cannot be replaced later.

The stream page separates received, shadow-processed and account-processed
sequences. Market ticks advance account progress without creating empty ledger
entries. Trades, order observations and account errors use the same position book
as query ingestion; repeated partial fills do not increase positions twice.
Unknown account replies stop shadow decisions, but subsequent actual replies are
still retained and booked. A persistence failure ends reception with its saved
tail intact rather than silently skipping it.

After interruption, “补处理已保存账户回报” processes only the selected retained
upper bound. It may bind an unbound stream to the existing account baseline,
never reconnects, replays old strategy targets or enables sending. Retrying the
same bound cannot book a trade twice. Reads, startup and restore never catch up
automatically. Pending account-changing replies must be handled before appending
a later query to the ledger.

```sh
northstar broker-catchup-stream BASELINE_UUID STREAM_UUID --through-sequence SEQUENCE \
  --request-id CATCHUP_UUID
northstar broker-funds BASELINE_UUID QUERY_UUID --request-id MONEY_REQUEST_UUID
northstar broker-funds-show MONEY_REQUEST_UUID
```

On a saved query page, record its account money observation. The money book
retains reported balances, available funds, margin/freezes and cumulative amounts,
with changes from the preceding observation and from the original baseline.
Commission 0 → 5 → 7 produces interval changes 5 and 2, not charges of 5 and 7.
`Balance` is not debited again, and `Available` is not reduced again by reported
freezes. `OBSERVED` means valid reported amounts, not reconciliation or permission
to spend them; unknown results use CLI exit code 2, observed results use 0.

Source identity, currency, futures business, trading day and settlement scope must
be confirmed before comparing. Missing fields remain unknown; cumulative reversals
are unresolved adjustments, not inferred refunds. Account receipt time is not an
atomic snapshot time or proof that particular trades are included. Actual per-fill
fees remain unknown: rates and account cumulative commission are not substituted.
These commands require Live authentication but no broker credentials or new connection, do not advance
shadow decisions, and create neither orders, simulated fills nor reservations.
Account progress describes only local handling of saved asynchronous replies.
It does not merge startup query results or prove missing replies were recovered;
`READY` is still `UNRECONCILED`. Independent position/order checks and funds
observations remain necessary. Funding/settlement reconciliation, persistent
reservations and explicit execution authority are still unfinished.
After backing up the running database, explicitly run `northstar init-db` with
the current version to add the account-progress table. Joint restore verifies its
fixed baseline, source prefix and ledger references without connecting or booking.
This is an independent engineering slice of #32, not external simulation acceptance.

## Command line

With Python 3.12, `uv`, and PostgreSQL 17:

```sh
uv sync --locked
export NORTHSTAR_DATABASE_URL='postgresql+psycopg://northstar:northstar_local@127.0.0.1:15432/northstar_quant'
export NORTHSTAR_DATA_DIR='/absolute/private/northstar/sources'
uv run northstar init-db
uv run northstar import examples/intraday.toml
uv run northstar datasets
uv run northstar run examples/intraday.toml
uv run northstar list
uv run northstar init-live-auth /absolute/private/northstar/runtime-auth
export NORTHSTAR_LIVE_AUTH='/absolute/private/northstar/runtime-auth/live-web.toml'
export NORTHSTAR_LIVE_URL='http://127.0.0.1:18081'
# In another terminal, with the same database and data directory:
# NORTHSTAR_LIVE_AUTH=/absolute/private/northstar/runtime-auth/live.toml uv run northstar live
uv run northstar live-web
```

`northstar show RUN_ID` reads a persisted result; `northstar replay RUN_ID`
uses its fixed snapshot and full saved configuration. The input file path is
relative to the study TOML, not the shell directory. The example is explicitly
synthetic and demonstrates the working application; it is not market evidence.

`northstar dataset SNAPSHOT_ID` shows fixed source and quality evidence.
`northstar research SNAPSHOT_ID --study examples/intraday.toml` uses only the
study's research parameters and the accepted snapshot; it does not read or
re-import the CSV. Without `--study`, it uses explicit research defaults.
The browser provides the same selection without copying a UUID, and can reuse
source/contract metadata for another import with an explicitly selected new file.

## Retained sources and processing

Uploading uses the file's actual bytes, including BOM or invalid UTF-8; parsing
happens only after permitted reception. Declare retention permission, permitted
local download, a usage basis, and whether the file was received directly or
converted outside this application. A converted file may link an actually retained
upstream source and a transformation note; the app does not invent an upstream
archive or claim it executed that conversion. Declarations are not independently
verified licenses, and local download permission is not redistribution permission.

`/sources` lists received files, processing and bounded pre-admission rejections.
Every accepted attempt fixes input, parameters and the actual implementation.
Invalid parameters, format or quality remain visible. Fix parameters on its detail
page and reprocess retained bytes; a broken file needs a separately uploaded
corrected file. Only confirmed publications with intact evidence are offered for
new research or Paper. Historical saved reports remain visible when bytes are
missing, with a source status explaining why re-execution is unavailable.

`northstar import STUDY` returns the processing attempt, not an implied successful
dataset; `PUBLISHED` carries `snapshot_id`. Each invocation creates a new attempt;
use `--request-id UUID` to retry an uncertain acknowledgement without repeating it.
The study's import-only `[archive]` table explicitly declares `use_basis`,
`allow_retention`, `allow_download`, `input_kind`, and optional
`upstream_source_id`/`transformation_note`; see the synthetic example.
Research-only commands do not need that table or the operator's original file.

```sh
northstar sources
northstar source SOURCE_UUID
northstar attempt ATTEMPT_UUID
northstar reprocess SOURCE_UUID --study examples/intraday.toml --request-id REQUEST_UUID
northstar download SOURCE_UUID /absolute/new-output.csv
northstar audit-data
```

Reprocessing records another attempt but reuses confirmed products for the same
input, parameters and implementation. It never replaces the original receipt,
availability declaration or an existing decision. Source and research pages show
the links through processing and publication to research/Paper consumers.

The single-file limit is 5 MiB (8 MiB HTTP envelope). The archive defaults to
10 GiB and leaves at least 256 MiB free; set `NORTHSTAR_ARCHIVE_MAX_BYTES` and
`NORTHSTAR_ARCHIVE_MIN_FREE_BYTES` to explicit local limits. Files are digest-named,
verified and flushed before their database reference is committed. A failure in
between may leave an unreferenced complete file or staging material, never a
partly published source. `audit-data` identifies these and marks interrupted
processing failed; it does not delete anything. The source list, detail, audit and
processing results expose unavailable files and capacity failures.

### Joint backup and empty-environment restore

The Docker image includes PostgreSQL 17 client tools; a local Python installation
also needs compatible `pg_dump`, `pg_restore`, `createdb` and `dropdb` for the full
installation acceptance. Normal app operation does not require a local server binary.

```sh
docker compose exec data-hub northstar backup /var/lib/northstar/backups/manual-001
```

This uses a maintenance gate for source processing and one exported PostgreSQL
snapshot for the dump and file reference list. The destination must be new and
separate from the live source directory. A completed backup contains
`database.dump`, `manifest.json`, and its own verified `sources/` bytes. Backup
references remain recorded; neither live sources nor valid backup references
have a destructive cleanup entrypoint.

For restore, explicitly create an **empty database**, set `NORTHSTAR_DATABASE_URL`
to that database and `NORTHSTAR_DATA_DIR` to a **new, independent absolute path**,
then run `northstar restore /absolute/backup-directory`. Do not initialize the
target first. Restore preflights every referenced file and the dump, never drops
or overwrites an existing database, and checks source/processing/publication
relations, all saved broker query evidence, baseline/comparison references and
the position-ledger, order-review and continuous-reception source/step chains.
An incomplete restore blocks normal startup. These are integrity checks, not a new
broker observation; restored comparisons never establish current account safety.
Recovered Paper remains paused and streams remain unattached; this is evidence recovery, not broker
reconciliation or live re-arming. Those remain separately gated future work.

## Recoverable file Paper

Open `/paper`, save a named strategy/Risk configuration, choose an accepted
dataset, and create a paused independent simulated account. “核对并推进下一条”
checks the committed fill ledger and advances exactly one accepted observation.
The page shows its fixed configuration, cash, positions, fees, curve, pending
authorization and input cursor. Saving another configuration does not change
an existing account. Reopening or restarting never advances it automatically.

This is explicitly `FILE_REPLAY`, not continuous market reception, a broker
simulation or live trading. Input exhaustion does not liquidate residual positions.
Only one accepted DAY session, minute bars and the current full-fill model are
supported. Browser operations require a short-lived browser session and CSRF
token; after a process restart, reopen the page before taking another step.

The CLI provides the same bounded behavior:

```sh
northstar configure examples/intraday.toml --name intraday
northstar configurations
northstar paper-create SNAPSHOT_ID CONFIGURATION_ID --request-id REQUEST_UUID
northstar paper-next SESSION_ID --request-id ANOTHER_REQUEST_UUID
northstar paper-show SESSION_ID
```

Use a fresh UUID for each intended command and reuse that UUID when retrying an
uncertain response. Each committed step is idempotent; a retry does not consume
another observation. Continuing a saved account requires its exact implementation
identity. Historical evidence remains readable, without an old-code compatibility path.

## Data imports

Research parameters have explicit defaults and reject unknown fields. Money,
prices and ratios are decimal strings; counts are integers. The application
derives contract identity, price economics, simulated positions, equity and mark
prices from the accepted data and execution ledger.

CSV columns are exactly `event_time,available_at,source_record_id,open,high,low,close,volume`.
`event_time` is the minute's start. `available_at` must be at or after its
completion, with its evidence and limitations declared in `[source]`:

- `source_reference`: declared acquisition/file reference, distinct from the
  application's actual archived bytes and upstream relationship.
- `availability_basis`: `SOURCE_DECLARED` for source times supplied by the
  operator (not independently verified), `FINAL_REVISED` for retrospective
  exploration, or `SYNTHETIC` for generated engineering examples.
- `availability_note`: the evidence or explicit assumption. `FINAL_REVISED`
  requires `available_at = event_time + 1 minute`, a simulated observation clock,
  not proof of historical publication. Download time must not replace it.

Prices and volumes are plain decimals; every price must align to the declared tick. The
explicit session determines expected coverage; the file cannot define its own
quality expectations by omitting missing bars.

The first real file comes from [Shinny EDB](docs/data-source.md): the actual
`SHFE.rb2610` contract's 2026-09-04 afternoon session, 90 one-minute bars. It is
retrospective data, not a point-in-time certified feed. Downloaded market files
stay in a manually prepared local `.northstar/` evidence bundle and are not
redistributed in this repository. That earlier manual bundle is not itself the
managed archive; current imports explicitly retain the received file in Data.

## Planned data and workspace management

The [lifecycle design](docs/ARCHITECTURE.md#8-持久化界面与运行维护) assigns managed source
files, processing attempts, publication and usage tracking to Data; Research owns
jobs and experiments, and Live owns authoritative trading facts. PostgreSQL stores
each owner's records; durable files hold source bytes and large data products.
In the target deployment, local research storage and cloud Live storage are backed
up independently with their referenced files; cross-role archives are fixed copies,
not mutable truth.
The bounded source→processing→publication→research
path and its joint backup/restore are implemented, as are fixed strategy/Risk
revisions and Paper bindings. Factor-result management, larger data products,
broader policy management and live controls remain future work.

The workspace will expose data, actual factors, strategy configurations, Risk and
research/trading runs. Each run binds exact data, configuration and implementation
identities; editing a configuration does not change a Paper session or grant live
execution authority. These capabilities follow the live-first development order,
not separate management-platform milestones.

## Model scope

The current simulation uses one contract and one explicit intraday session of
one-minute bars. A completed bar becomes available at its supplied availability
time. Existing authorizations may fill at a subsequent observed close, adjusted
by declared tick slippage, before the next strategy decision. Per-lot fees are
actually deducted; Risk bounds and account constraints apply to execution.
Fees, slippage and margin fractions are explicit model assumptions, not verified
historical exchange or broker terms.

Results report realized and unrealized PnL, remaining exposure, fees, equity and
drawdown. They do not assume a final liquidation, exchange settlement, partial
fills or market impact. The strategy is a baseline momentum direction with a
deadband and explicit target exposure, not a profitability claim. There is no
annualized Sharpe inferred from a single session. Research does not consume the
bounded SimNow query as a live feed.

Accepted input snapshots and results are immutable. A run records its full
configuration, snapshot identity and an implementation fingerprint of source,
dependency lock, Python runtime and actual installed runtime dependencies.
Reproducing a run requires those exact inputs and the current implementation.
This greenfield application has neither backward nor forward compatibility.

## Development

`make verify` runs the checks that protect implemented behavior.
`NORTHSTAR_TEST_DATABASE_URL` must point to a disposable PostgreSQL database named
`northstar_quant_test`; tests replace its data. Never point tests at application
data. `uv build` produces one installable application wheel with its current
migrations and dependency-lock identity.

### VS Code

The checked-in `.vscode` setup uses the repository's locked `uv` environment,
Ruff, mypy and pytest. Install the recommended extensions and run the
“Northstar: sync dependencies” task after a fresh checkout. If VS Code has
retained another interpreter, use “Python: Select Interpreter” and choose the
repository `.venv`. The Test Explorer, test and verify tasks use the disposable
local Compose database
`northstar_quant_test`; its schema is deliberately reset by pytest. The tasks
create it together with an isolated `northstar_quant_vscode` database for native
debugging, never using the containerized application's `northstar_quant` data.
Run “Northstar: prepare isolated development databases” once before using Test
Explorer; the test and verify tasks run that preparation automatically.

Use “Northstar: Debug independent Live and Live Web” or the individual Live Web
(18082) and Live (18083) configurations for breakpoints in separate processes.
They prepare private runtime authentication, the isolated database and its archive at
`.northstar/vscode-sources`; this is intentionally separate from the Docker
application and its managed volumes on ports 18080/18081. Both configurations leave
`NORTHSTAR_SIMNOW_CONFIG` empty, so it neither loads broker credentials nor
connects to a broker. Do not add a private SimNow file to `.vscode/test.env`.

CI also installs the wheel with locked runtime dependencies in a separate
environment and exercises the installed CLI and HTTP application from an empty
working directory. It checks the synthetic study's accounting, repeated runs,
exact replay, packaged web assets and persistence across application restarts.
It also checks installed configuration/Paper operations, command retry identity,
paused process restart and exact agreement with the batch simulation account.
On Linux amd64 it also checks the installed native CTP create/release path without
network access. Installation checks explicitly discard the operator's private
SimNow configuration; passing CI is not broker login or simulated-trading evidence.
The same check runs inside the runtime-only Docker image, without a source mount:
`python scripts/check_install.py examples/intraday.toml` (use the installed
environment's Python and the disposable test database). This is a real HTTP
check; it does not replace browser interaction acceptance.

Detailed behavior lives in code. [Architecture](docs/ARCHITECTURE.md) explains
the organization and assumptions; [development order](docs/ROADMAP.md) explains
the stages. [GitHub Project](https://github.com/users/isqiwen/projects/1) tracks
the issues, priorities and dependencies. [AGENTS.md](AGENTS.md) contains the
permanent engineering rules: current-only design, flexible topology, vertical delivery, cohesive code
and a small behavior-focused test budget.
