# Standalone Live deployment

This is the current Live-only topology, not a second implementation. It runs the
same installed Python package as Data Hub and Research, with its own
database, source files and deployment authentication. Next.js, the Python management API and the kernel run independently. It does not start or mount
Data Hub or Research. Do not reuse the personal application's volumes or attach
its database network. Never run two instances against one broker account.

This is a **no-broker-credentials engineering baseline**, not cloud or trading
acceptance. Fixed strategy/contract/warm-up material delivery, a selected cloud
host and external alert channel remain #40 work. Startup does not connect a
broker, reconcile an account or authorize orders. Do not attach broker secrets
until the deployment and connection scope are separately approved.

## Start from an installed image

Use a previously verified Linux amd64 image by registry digest. Local acceptance
may instead use `make up-live` to build from the current source. Compose has build definitions;
use `up --no-build` for deployment of already-verified images. The default password
`northstar_local` and 1 GiB memory budgets are local development defaults, not production sizing.
The repository maintains defaults in [live/.env](.env). Copy it to the target host’s
private owner-only environment file, fill credentials there, and keep real passwords out of Git.
The supported settings are:

- `NORTHSTAR_LIVE_FRONTEND_IMAGE`: the exact tested Next.js frontend image.
- `NORTHSTAR_LIVE_IMAGE`: the exact approved image reference.
- `NORTHSTAR_LIVE_DATABASE_PASSWORD`: a generated URL-safe password (for example,
  `secrets.token_urlsafe(32)`); never place it in an Issue or shared command output.
- `NORTHSTAR_LIVE_KERNEL_MEMORY`, `NORTHSTAR_LIVE_DATABASE_MEMORY`: explicit memory
  budgets established from the intended workload. The kernel has no fixed CPU quota.
- `NORTHSTAR_LIVE_WEB_PORT`: optional loopback port, default 18080.

Run on the intended host after deployment authorization:

```sh
docker compose --env-file /absolute/private/live.env -f deploy/live/compose.yaml up -d --no-build
docker compose --env-file /absolute/private/live.env -f deploy/live/compose.yaml ps
```

Do not print rendered Compose configuration: it contains the database password.
Changing that variable does not rotate the password of an existing database.
Keep the same project identity for its volumes; never use `down -v` on retained data.

Live Web and its API (default 19080) are published on loopback for local development. PostgreSQL and the kernel have no host
ports; the frontend and management API have no storage network, database settings, sources or broker secrets.
For initial private management, use an authenticated SSH tunnel to that loopback
port, preserving localhost Host/Origin for HTTP and WebSocket. Do not expose the
raw Web publicly or weaken same-origin checks. A reverse proxy/public endpoint
needs its own authentication, TLS and WebSocket verification before use.

Web readiness only means its HTTP process is serving. Query `/api/live/status`
through the normal same-origin session or run `northstar status` inside the
kernel for its actual identity/storage availability. Neither proves tradability.
Web restart is independent; kernel/database restart never restores sending authority.
Automatic container restart is not an external host-loss alert.

## Operational logs

The persistent `logs` volume is mounted at `/var/log/northstar` in the API and kernel.
They write `live/northstar-live-api-YYYY-MM-DD.log` and
`live/northstar-live-kernel-YYYY-MM-DD.log` independently. The date follows the process
timezone and switches on the first background write after midnight. Files rotate at
10 MiB; each program retains at most five historical files across dates and size
rotations, ordered by modification time. Replace `YYYY-MM-DD` below with the log date:

```sh
docker compose --env-file /absolute/private/live.env -f deploy/live/compose.yaml exec live tail -n 50 /var/log/northstar/live/northstar-live-kernel-YYYY-MM-DD.log
```

The bounded asynchronous writer drops operational records when saturated, counts
loss and disk errors, and never falls back to synchronous kernel disk writes.
`northstar check` includes log health; it does not grant or revoke trading authority.
Log retention and CPU/disk contention must be measured on the deployed host. Keep
trading facts in their durable business stores; log files are not an audit ledger.

## Reproducible local acceptance

```sh
uv run --project backend python scripts/acceptance/check_live_deployment.py --image northstar-quant:local --frontend-image northstar-live-frontend:local
```

The check creates a uniquely named, isolated Compose project with generated test
authentication and an ephemeral loopback port. It verifies installed pages,
runtime identity across Web restart, database failure and kernel disappearance,
then deletes **only its own disposable containers, networks and volumes**.
It never loads SimNow credentials, invokes broker operations or stops the personal
application. This is actual Docker/HTTP acceptance, not a YAML/layout test and not
evidence that a cloud host or real browser session was tested.
