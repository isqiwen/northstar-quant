---
name: northstar-runtime-verification
description: Verify or diagnose Northstar installed applications, persistence and Web/worker lifecycle isolation using the existing acceptance paths. Not for pure computation changes or routine documentation edits.
---

# Northstar runtime verification

Resolve paths from this Git repository's root. Start with the claimed behavior and
environment, not a blanket restart. Diagnosis alone authorizes checks, not fixes,
personal-service changes or new broker activity.

Read `Makefile`, `.github/workflows/ci.yml` and the relevant README section for
current commands and dependencies; do not copy stale test counts or image tags.

| Claim | Existing path and meaning |
|---|---|
| Persisted business behavior | Affected tests against disposable PostgreSQL; inspect `backend/tests/conftest.py` before selecting the database |
| Clean installation and cross-app research/Paper flow | Build/install a fresh wheel as CI does; run `scripts/acceptance/check_install.py` with the installed interpreter, not the source environment |
| Standalone Live supervision/storage failure | Read `deploy/live/README.md`; run `scripts/acceptance/check_live_deployment.py` with the image built from the change under verification |
| User can operate a page | Actual browser interaction; HTTP 200 does not execute React controls or verify dynamic API interactions |
| Current broker/cloud behavior | Explicitly scoped external acceptance; local containers, replay and stored callbacks cannot establish it |

Inspect the selected script's side effects, inputs and cleanup targets before
running it. Reuse its mechanics instead of writing another orchestration script.
Use unique disposable database/container identities, source directories and ports;
personal volumes, saved market evidence and active services are not test resources.
Never load SimNow credentials just to verify topology. Missing PostgreSQL clients
can skip restore checks: report the skip or supply the declared tools and rerun,
not a blanket "all persistence checks passed". Keep private config out of output.

For a restart claim, observe independent runtime identity and progress before and
after restarting only the management Web. For failure, check both the unavailable
owner and the still-serving management process; readiness is not tradability.
A restarted kernel must not silently create another receiver or inherit execution
authority. Use the existing bounded acceptance, not YAML or PID counts alone.

Distinguish synthetic research, final-revised history, reconstructed capture,
internal Paper and external broker facts. Preserve original evidence; adapt a
private configuration copy to the current format when necessary, never add
compatibility merely to make an old acceptance command succeed.

Report tested commit/image, checks actually run, skips/unknowns and cleanup outcome.
Remove only the disposable resources this run created; leave the personal deployment
unchanged unless its operation was explicitly in scope.


## Data browsing acceptance

For Data Hub discovery changes, exercise `scripts/acceptance/check_browser.py` from
an ordinary `/browse` entry: select a contract with a nonempty publication without manually supplying
contract/dates, inspect the resulting chart and exact rows, then recover from an
empty manual date range using the available-data list. Search by a supplied Chinese
contract name and confirm the unchanged supplier code. For chart changes, switch
only available native periods and verify their populated dates, inspect whole-range chart counts
across detail pagination, and exercise zoom/indicator controls. Chart presentation
must not infer realtime prices or replace exact source values with indicator floats. Contract catalog membership,
sync counters and a nonempty publication request window are not proof that a chosen
date range contains rows. The owning `exploration.discovery` entry selects a current native publication and opens a pinned
receipt through the same file verification as `exploration.rows`; PostgreSQL tests
in `backend/tests/data_management/test_exploration.py` cover that boundary. Use the
existing synthetic installed fixtures; do not depend on today's date or personal
historical downloads for repeatable browser acceptance.

For the contract workbench, assert one row per contract across native periods. Open
the date-range drawer for manual ranges and the detail drawer for raw rows/source
checks; close drawers before chart interactions. Ordinary browse re-entry restores
the last contract/period in the tab; explicit receipt links take precedence.

Exercise detail pagination for both native and compacted views. A parent that
clears the current result during every page fetch unmounts the drawer and loses
its open state; preserve the current fixed view until the next page arrives,
while clearing it when the owning artifact identity changes.
