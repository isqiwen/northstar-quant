# Northstar Quant — Master Implementation Plan

> This document is the durable implementation control record for the Northstar
> Quant repository suite. [GitHub Project 1](https://github.com/users/isqiwen/projects/1)
> is the live delivery queue. Domain repositories own all source code, tests,
> configuration, runtime assets, and release evidence.

## Control-plane rules

For every work package, record one owner repository, one Project item, explicit
dependencies and exclusions, and verifiable acceptance criteria. The owner
repository implements and validates the change. Mark a package `DONE` here only
after its PR is merged and the Project item agrees.

Current status terms:

- `TODO`: authorized but not started;
- `IN_PROGRESS`: the owner is implementing it;
- `VERIFY`: implementation exists but a stated check still remains;
- `BLOCKED`: an external prerequisite prevents safe work;
- `DONE`: owner evidence is merged and the Project item is done.

## Current state

~~~yaml
active_phase: null
active_work_package: null
next_task: null
blocked_work_packages: [P10-WP08, P10-WP09, MAINT-WP02]
~~~

There is no currently authorized executable work package. Do not infer a new
implementation mandate from this repository, a parent issue, or a completed
pull request.

## Latest delivery closure

### P15-DH-01 — Data Hub Import Quality Applicability

**Status:** DONE

**Owner:** [quant-data-hub](https://github.com/isqiwen/quant-data-hub)

**GitHub Project item:** [quant-data-hub#10](https://github.com/isqiwen/quant-data-hub/issues/10)

**Merged evidence:** [quant-data-hub#30](https://github.com/isqiwen/quant-data-hub/pull/30)
merged as `1dbd3f975bbc9df0c6473e7c44a6815497e154eb` on 2026-09-04. Its
`backend-quality` check passed; #10 is closed and its Project item is `Done`.

## External blocks

### P10-WP08 — Platform Production / DR Acceptance

**Status:** BLOCKED

**Owner:** [quant-ops](https://github.com/isqiwen/quant-ops)

**Prerequisites:** An authorized Linux production host and deployment window,
production DR policy, managed PostgreSQL prerequisites, encrypted offsite
backup/WAL/PITR, documented RPO/RTO, and a controlled recovery drill.

**Fail-closed boundary:** No production or live-trading action is authorized.
Local or simulated recovery evidence is not production acceptance.

### P10-WP09 — Authoritative Data & Source Onboarding

**Status:** BLOCKED

**Owner:** [quant-data-hub](https://github.com/isqiwen/quant-data-hub)

**Prerequisites:** Auditable data licence and source authorization, authoritative
contract/calendar/rule artifacts, and production point-in-time source evidence.

**Fail-closed boundary:** Fixtures, synthetic data, public exploration sources,
or `ctp_sim` do not establish authoritative-data or trading authorization.

### MAINT-WP02 — Native Linux PostgreSQL Development Verification

**Status:** BLOCKED

**Owner:** [quant-ops](https://github.com/isqiwen/quant-ops)

**Prerequisite:** An authorized, disposable Ubuntu/Debian host without the
required PostgreSQL/client packages, on which the owner repository's controlled
workstation setup can be validated without modifying existing services or data.

**Fail-closed boundary:** Do not substitute Docker, hosted CI, or destructive
database operations for this verification.
