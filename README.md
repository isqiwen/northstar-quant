# Northstar Quant

A personal domestic-futures trading system targeting controlled live execution.
One repository, one Python package, one PostgreSQL database. The delivery path is
read-only broker access → broker simulation → recovery and reconciliation → a
bounded, explicitly authorized live round trip; advanced research comes later.

Currently implemented: import minute bars, replay a strategy with Risk and trading
costs, and inspect persistent results in your browser. Broker connectivity and
live execution are not implemented. Research or Paper results never enable real
orders; see the [architecture and live gates](docs/ARCHITECTURE.md#9-首个受限实盘闭环).

## Run

```sh
docker compose up --build -d
```

Open <http://127.0.0.1:18080>. The browser and database bind to local loopback;
the Compose credentials are local development credentials. `docker compose down`
stops the application and keeps its data volume.

The workspace imports a bounded CSV with explicit source, contract and session
metadata. Select accepted data directly from the library, including after a
restart, inspect its source and quality, then research without re-uploading the
file. Reports show the saved equity curve, fills, costs, holdings and risk
decisions alongside fixed input evidence. Empty state contains no invented results.

## Command line

With Python 3.12, `uv`, and PostgreSQL 17:

```sh
uv sync --locked
export NORTHSTAR_DATABASE_URL='postgresql+psycopg://northstar:northstar_local@127.0.0.1:15432/northstar_quant'
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

Research parameters have explicit defaults and reject unknown fields. Money,
prices and ratios are decimal strings; counts are integers. The application
derives contract identity, price economics, simulated positions, equity and mark
prices from the accepted data and execution ledger.

CSV columns are exactly `event_time,available_at,source_record_id,open,high,low,close,volume`.
`event_time` is the minute's start. `available_at` must be at or after its
completion, with its evidence and limitations declared in `[source]`:

- `source_reference`: original source/file reference; retain the original file
  separately, as PostgreSQL keeps accepted observations and byte hashes, not CSV bytes.
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
redistributed in this repository. This is not an application-managed source archive;
the current Compose deployment persists PostgreSQL only.

## Planned data and workspace management

The [lifecycle design](docs/ARCHITECTURE.md#8-持久化界面与运行维护) extends the same
application with managed source files, processing attempts, publication and usage
tracking. PostgreSQL owns records and trading facts; durable files hold source
bytes and large data products. Raw archives, complete failed-import tracking,
factor-result management and reusable strategy/Risk configuration revisions are
not yet implemented. Continue retaining original files separately for now.

The workspace will expose data, actual factors, strategy configurations, Risk and
research/trading runs. Each run binds exact data, configuration and implementation
identities; editing a configuration will not change an active session or grant live
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
annualized Sharpe inferred from a single session, live data feed or broker link.

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
