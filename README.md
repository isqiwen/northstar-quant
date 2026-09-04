# Northstar Quant Control Plane

This repository governs the architecture and delivery of nine independently
owned domain repositories. It contains no product runtime or shared library.

- [Architecture](docs/ARCHITECTURE.md): authorities, interfaces, adapters, and
  cross-repository seams.
- [Repository map](docs/REPOSITORY_MAP.md): current maturity and ownership.
- [Roadmap](docs/ROADMAP.md): the new rolling-wave task plan.
- [Project management](docs/PROJECT_MANAGEMENT.md): live field and lifecycle
  rules for [GitHub Project 1](https://github.com/users/isqiwen/projects/1).

The former monolithic implementation remains recoverable from
[`backup/pre-control-plane-20260904`](https://github.com/isqiwen/northstar-quant/tree/backup/pre-control-plane-20260904).
The pre-reset project plan and its phase identifiers are intentionally not part
of the new architecture.
