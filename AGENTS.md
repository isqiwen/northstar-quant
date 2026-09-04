# Northstar Quant Control Plane

`northstar-quant` is the implementation control plane for nine domain
repositories. It owns architecture topology, roadmap ordering, acceptance
policy, and GitHub Project state. Domain repositories own all executable code,
contracts, data, tests, deployment assets, and runtime authority.

## Start here

For every non-trivial request:

1. Read [the architecture](docs/ARCHITECTURE.md) when ownership, an interface,
   an adapter, or a cross-repository seam may change.
2. Read [the roadmap](docs/ROADMAP.md) and select only a work package whose
   dependencies are satisfied.
3. Read [the Project rules](docs/PROJECT_MANAGEMENT.md), then verify the live
   fields in [GitHub Project 1](https://github.com/users/isqiwen/projects/1).
4. Use [the repository map](docs/REPOSITORY_MAP.md) to enter the owner
   repository and read its local `AGENTS.md` before implementation.
5. Run `git status` in every repository you will touch and preserve unrelated
   user changes.

Completion means every step above was checked against current default-branch
and Project evidence, not remembered chat or a superseded issue.

## Routing

The owner named in the architecture owns the domain meaning and publishes its
interface. A consumer owns its compatibility policy and adapter. Cross-repository
work uses immutable artifacts or authenticated network interfaces; repositories
do not import one another's source or read one another's database.

This repository accepts only architecture revisions, roadmap changes, Project
policy, ownership maps, dependency decisions, and merged-evidence links.
Implementation changes are made and tested in the owning domain repository.

## Project lifecycle

- `Backlog` means planned but not dependency-ready.
- `Todo` means dependency-ready and eligible to start.
- `In Progress` means active implementation; limit three globally and one per
  repository.
- `Review` means an owner PR exists and is awaiting required checks or merge.
- `Done` requires a merged owner PR and satisfied acceptance evidence.

Every Project item must have Status, Priority, Kind, Effort, Target, owner
repository, dependencies, exclusions, and acceptance evidence. Advance status
only after checking the linked evidence. Closing or merging an unrelated issue
does not authorize another work package.

## Safety

Northstar Quant is real-money-adjacent. Research artifacts, historical
simulation, Paper, and SimNow never imply real-money authority. Unknown data,
account, position, order, risk, broker, approval, or environment state remains
fail-closed and permits no new risk.

## Control-plane verification

Before opening a control-plane PR:

1. Check all Markdown links and GitHub references.
2. Run `git diff --check`.
3. Confirm the architecture dependency graph has no contract-publication cycle.
4. Confirm the roadmap and every live Project field describe the same status.
