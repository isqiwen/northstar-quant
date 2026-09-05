# Northstar Quant

A personal domestic-futures trading system targeting controlled live execution.
One repository, one Python package, PostgreSQL and a managed private source directory. The delivery path is
read-only broker access → broker simulation → recovery and reconciliation → a
bounded, explicitly authorized live round trip; advanced research comes later.

Currently implemented: retain original uploaded bytes, inspect processing failures
and publication, import minute bars, replay a strategy with Risk and trading
costs, save fixed configurations, and advance a recoverable file-driven Paper
account in your browser. A concrete CTP adapter now provides explicit bounded
SimNow read-only queries and saved browser evidence. On 2026-09-05, the approved
`simnow_dev` account passed actual TD authentication/login, all seven TD queries
returned terminal replies, and the selected contract subscription produced one market observation.
This is not a continuous or freshness-verified feed; see the
[acceptance evidence and remaining gaps](docs/broker-source.md).
Broker orders and live execution are not implemented. Research, Paper and successful queries never enable real
orders; see the [architecture and live gates](docs/ARCHITECTURE.md#9-首个受限实盘闭环).

## Run

```sh
docker compose up --build -d
```

Open <http://127.0.0.1:18080>. The browser and database bind to local loopback;
the Compose credentials are local development credentials. `docker compose down`
stops the application and keeps its data volume.
Compose persists database, managed source and backup volumes separately; retain
database records and their referenced files together. Container rebuilds do not
discard sources. Neither uploaded market files nor backups belong in public Git.
Initialization accepts only the current data baseline; it never resets an
existing database. After a backup, `northstar init-db` can add a Module's new
tables when existing fact shapes are unchanged. A replacement of existing storage
shapes requires explicitly preserving needed evidence and preparing the current
storage, not an in-place legacy compatibility path.

The workspace imports a bounded CSV with explicit source, contract and session
metadata. Select accepted data directly from the library, including after a
restart, inspect its source and quality, then research without re-uploading the
file. Reports show the saved equity curve, fills, costs, holdings and risk
decisions alongside fixed input evidence. Empty state contains no invented results.

## SimNow connection

The current deployment is one **Linux amd64** application with `ctpwrapper==6.7.13`.
Compose selects that architecture, including on an Apple Silicon host. Native
macOS/arm64 remains usable for research and inspecting saved records, but not CTP.
The [SDK evidence and limitations](docs/broker-source.md) separate offline native
verification from actual authentication and account-query acceptance.

In your own terminal, run the private configuration wizard:

```sh
bash scripts/setup_simnow.sh
```

It saves literal values in owner-only `.northstar/simnow.env`, excluded from Git
and Docker build context. Do not source this file, paste its contents into chat,
or add credentials to HTTP requests. The wizard does not connect or trade.
For a database already on the current baseline, attach that file to the same app:

```sh
docker compose -f compose.yaml -f compose.simnow.yaml up --build -d
docker compose exec app northstar broker-sdk-check
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

For a native Linux amd64 installation, set `NORTHSTAR_SIMNOW_CONFIG` to the
absolute private file path. Reusing a request UUID returns the fixed query,
including a failed or interrupted one; a deliberate new query needs a new UUID.
Only one query per environment/account runs at a time. A native crash or timeout
is confined to a short-lived child inside the application, with no separate
service. Saved final evidence survives restart; an interrupted parent leaves
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
uv run northstar serve
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
docker compose exec app northstar backup /var/lib/northstar/backups/manual-001
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
relations, all saved broker query evidence and baseline/comparison references.
An incomplete restore blocks normal startup. These are integrity checks, not a new
broker observation; restored comparisons never establish current account safety.
Recovered Paper remains paused; this is data/research recovery, not broker
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

The [lifecycle design](docs/ARCHITECTURE.md#8-持久化界面与运行维护) extends the same
application with managed source files, processing attempts, publication and usage
tracking. PostgreSQL owns records and trading facts; durable files hold source
bytes and large data products. The bounded source→processing→publication→research
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
