---
name: northstar-research-validation
description: Validate Northstar fixed research runs and backtest results, including causal inputs, futures ledger reconciliation and reproducibility. Use for research acceptance or investigating suspicious results, not Data Hub throughput or broker execution acceptance.
---

# Northstar research validation

Resolve paths from the repository root. Read `docs/ARCHITECTURE.md` sections on
research inputs and evaluation, then the owning Issue's acceptance scope in
`docs/ROADMAP.md`. A single real contract can establish the first cross-day
research path; full-market synchronization is not a prerequisite.

## Select the existing path

| Question | Owning implementation |
|---|---|
| Run, replay or compare fixed inputs | `backend/src/northstar_quant/research/operations.py` and `runs.py` |
| Durable attempts, cancellation and restart | `backend/src/northstar_quant/research/tasks/` |
| Configuration and causal session processing | `backend/src/northstar_quant/research/configuration.py` and `backtesting/session.py` |
| Ledger-derived report and performance | `backend/src/northstar_quant/research/backtesting/report.py` and `performance.py` |
| Evaluation windows and trained inputs | `backend/src/northstar_quant/research/evaluation.py`, `learning.py` and `learning_execution.py` |

Use the current application API or `northstar research --help` for execution;
inspect its caller before assuming a command accepts a published snapshot.
Do not build a separate notebook simulator or record authoritative attempts in a
skill-owned Markdown state file. Missing application behavior is an implementation
gap, not a reason to bypass it by inserting rows directly.

## Establish what the result can prove

For a strategy study, state the hypothesis, baseline and rejection criteria before
interpreting returns. For an engineering acceptance, select the concrete behavior
to prove; a profitable strategy is not required. Record the actual contract,
snapshot/content identity, configuration revisions, code/install identity and
run/attempt identity from persisted results. Missing facts remain missing; model
assumptions must not become verified exchange terms.

Check bar completion, availability and trading day separately, including night
sessions and the applicable fee/margin/settlement revisions. Historical revised
data does not prove what was available live. Use the selected Tushare native
frequency; do not add cross-frequency comparison or replace it with aggregation.

Follow the report's identified orders, fills and settlement facts through fees,
today/yesterday positions, reservations, margin, cash and equity. Inspect partial
fills and remaining orders rather than assuming every signal executed. Explain
any discrepancy using its originating fact; do not recompute an unrelated equity
curve and treat agreement on Sharpe as ledger validation.

Replay with the saved code revision and fixed inputs through `ResearchOperations`;
it checks snapshot and result identity. For comparisons, preserve the owner's
input/evaluation compatibility checks. Apply held-out or walk-forward evaluation
when it answers the study question; freeze fitted parameters outside training and
record the trials actually searched. Missing statistical capabilities stay explicit
follow-up work rather than fabricated metrics or mandatory new infrastructure.

## Evidence and limits

Use relevant existing tests for the suspected failure. When installation or worker
isolation is part of the claim, inspect `scripts/acceptance/check_install.py` or
`check_browser.py` and follow the repository runtime-verification skill. These
acceptance paths use synthetic inputs and disposable storage; their success alone
does not validate a real supplier dataset or a broker round trip.

Report the persisted run, exact identities, checks performed, discrepancies and
remaining assumptions. Distinguish executed results from proposed code and numerical
examples. No checklist completion, role persona or nonempty signature establishes
research acceptance or trading authorization.
