# Northstar Quant Control Plane

This repository is the implementation control plane for the Northstar Quant
suite. It coordinates work across the domain repositories; it is deliberately
not an application, package, runtime, deployment, or shared-code repository.

The current implementation queue lives in [GitHub Project 1](https://github.com/users/isqiwen/projects/1).
The durable control record is the [master implementation plan](docs/planning/MASTER_IMPLEMENTATION_PLAN.md).

## What belongs here

- cross-repository work packages, dependencies, acceptance criteria, and status;
- domain ownership and integration seams;
- delivery and safety policy;
- links to merged implementation evidence and external prerequisites.

## What does not belong here

- product or domain source code;
- tests, package manifests, migrations, runtime configuration, data, or fixtures;
- deployment automation, environment bootstrap, or broker integrations.

Those assets belong to the repository that owns the relevant domain. See the
[repository map](docs/REPOSITORY_MAP.md) and [operating model](docs/OPERATING_MODEL.md)
before routing a task.

## Migration note

The former monolithic implementation is preserved at
[`backup/pre-control-plane-20260904`](https://github.com/isqiwen/northstar-quant/tree/backup/pre-control-plane-20260904),
created from `main` commit `f86850c`. The `main` branch is now reserved for the
control-plane role described above.
