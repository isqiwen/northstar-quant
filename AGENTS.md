# Northstar Quant

This repository is the sole maintained project for the personal futures research
system. The product scope is domestic Chinese futures: historical research and
continuous simulated trading first. Read `docs/ARCHITECTURE.md` for changes to
domain meaning, the shared trading loop, storage or runtime topology; read
`README.md` to run the application. Code documents implemented details.

## Permanent engineering rules

- Keep agent instructions in their owning Git repository. Never create
  `../AGENTS.md` in the non-repository workspace directory.
- Maintain one current project. Complete replacements by removing superseded
  projects, code, configuration and runtime resources; do not retain parallel
  legacy implementations, retirement-only repositories or compatibility layers.
- Greenfield: implement exactly one current architecture, storage model and
  protocol. There is neither backward nor forward compatibility. Replace all
  affected callers atomically and remove superseded code, tests and active prose.
  Immutable content identities preserve reproducibility, not old implementations.
- Repository count is a design choice. The current choice is one repository,
  one Python package, one application process and PostgreSQL. Modules call typed
  Python functions in process. Add another deployment only for demonstrated need.
- Deliver vertical behavior: source data → accepted immutable snapshot → repeated
  strategy and Risk decisions → simulated fills and account updates → persisted
  result → browser view. Measure useful running outcomes, not artifact counts.
- Keep types and invariant enforcement beside the behavior that owns them.
  Never create generic `contracts/`, `schemas/`, `validators/`, or `fixtures/`
  layers or renamed equivalents. Generate external descriptions only when used.
- Keep only tests for costly observable failures: causality, money, authorization,
  immutable data, reproducibility and real integration. Never test documents,
  Markdown, links, directory layout, trivial constants or private implementation.

## Domain rules

Data owns ingestion, source evidence, calendars, quality and immutable snapshots.
Its public research interface returns immutable values and hides SQLAlchemy.
Strategy maps point-in-time features to account-neutral target exposure. Risk
owns sizing and limits. Execution owns simulated fills, costs and account truth.
Apply already available account facts and effective market terms before using
an executable market event to fill a previously authorized order. Update the
account, observe data, decide, then authorize an order for a later event.

Use Decimal for financial values, explicit tick/multiplier/currency and the
canonical futures contract UUID. Preserve bar start, bar completion, availability
and trading day separately. Derive portfolio state from the ledger, never from
operator-supplied state. Persist complete inputs, data identity and implementation
identity with every result. State model limitations truthfully; research results
do not establish strategy profitability or live execution authority.

## Workflow

Inspect the worktree first and preserve unrelated user edits. Architecture and
implementation may change together in the same local work package; a separate
documentation merge is never an implementation dependency.
For product development, read `docs/ROADMAP.md`, then the selected issue and its
blocking dependencies in https://github.com/users/isqiwen/projects/1. Use issues
in this repository only, keep Project status aligned with observed progress,
and deliver one primary vertical outcome at a time. Record missing external
inputs without blocking independent work. Close issues only with reviewable
implementation and acceptance evidence; local work alone is not remote delivery.
Verify affected behavior and a real PostgreSQL vertical path when storage or
integration changes.
For agreed implementation tasks, routine edits, commits, non-forced pushes,
CI follow-up and Issue/Project updates are authorized without repeated approval.
Read-only questions authorize inspection, not implementation or publication.
Merges, releases, broker connections and irreversible data operations require
their own explicit task scope; routine development authority does not imply them.
