# Northstar Quant Control Plane

`northstar-quant` is the implementation control plane for the Northstar Quant
repository suite. It holds the cross-repository plan, ownership map, delivery
rules, and project-level decisions. It does not contain application code,
runtime configuration, deployments, migrations, tests, or data assets.

## Start here

For every non-trivial request:

1. Read [the master implementation plan](docs/planning/MASTER_IMPLEMENTATION_PLAN.md).
2. Check the linked GitHub Project item and the owning repository in
   [the repository map](docs/REPOSITORY_MAP.md).
3. Run `git status` and preserve unrelated user changes.
4. Work on exactly one active work package.

The master plan is the control plane's record of current work. GitHub Project
is the live delivery queue. A repository's own issue, source, tests, and CI are
the evidence for its implementation.

## Routing

Implementation belongs in the repository that owns the domain. When a work
package changes source, tests, schema, configuration, API, deployment, or
runtime behavior, switch to that owner repository and follow its local
instructions. Do not recreate that implementation in this repository.

Changes in this repository are limited to:

- the master implementation plan and work-package acceptance criteria;
- repository ownership and cross-repository dependency maps;
- delivery policy, governance, and explicitly approved architectural decisions;
- links to merged evidence and externally blocked prerequisites.

Keep each item concrete: owner repository, issue/PR link, dependencies,
acceptance criteria, and status. Use a cross-repository contract only for a
real seam; implementation details stay with the owner.

## Delivery lifecycle

1. Select the one authorized Project work package and confirm its dependencies.
2. Mark the Project item `In Progress` before implementation begins.
3. Implement and validate only in the owning repository.
4. Open a PR in that repository with the issue link, scope, exclusions, and
   verification evidence.
5. After the PR is merged, verify the merged commit and checks, then move the
   Project item to `Done`.
6. Update this master plan with the merged evidence, clear the active work
   package and `next_task` when appropriate, and open a control-plane PR for
   that documentation change.

An open PR, a green CI run, or a local commit is not completion evidence. The
control plane marks a work package done only after its owner-repository change
is merged and the Project state agrees.

## Safety and scope

Northstar Quant is real-money-adjacent. The control plane never enables live
trading, supplies credentials, grants production access, or converts research
or simulation evidence into trading authority. Unknown authorization, account,
risk, data, or broker state remains `NO NEW RISK`.

Do not add `src/`, `tests/`, runtime configuration, package manifests,
migrations, deployment scripts, or environment bootstrap code here. The
pre-control-plane implementation is recoverable from
`backup/pre-control-plane-20260904`.

## Control-plane verification

Before committing a control-plane change:

1. Check that every Markdown link and GitHub reference points to the intended
   owner, issue, or PR.
2. Run `git diff --check`.
3. Confirm the plan, GitHub Project state, and linked owner-repository evidence
   describe the same lifecycle state.
