# Standalone Live deployment

This is the current Live-only topology, not a second implementation. It runs the
same installed package as the local all-application Compose, but with its own
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
may instead pass an already-built local image; no source build occurs here.
Set these variables in a private owner-only environment file outside Git:

- `NORTHSTAR_LIVE_FRONTEND_IMAGE`: the exact tested Next.js frontend image.
- `NORTHSTAR_LIVE_IMAGE`: the exact approved image reference.
- `NORTHSTAR_LIVE_DATABASE_PASSWORD`: a generated URL-safe password (for example,
  `secrets.token_urlsafe(32)`); never place it in an Issue or shared command output.
- `NORTHSTAR_LIVE_KERNEL_MEMORY`, `NORTHSTAR_LIVE_DATABASE_MEMORY`: explicit memory
  budgets established from the intended workload. The kernel has no fixed CPU quota.
- `NORTHSTAR_LIVE_WEB_PORT`: optional loopback port, default 18080.

Run on the intended host after deployment authorization:

```sh
docker compose --env-file /absolute/private/live.env -f deploy/live/compose.yaml up -d
docker compose --env-file /absolute/private/live.env -f deploy/live/compose.yaml ps
```

Do not print rendered Compose configuration: it contains the database password.
Changing that variable does not rotate the password of an existing database.
Keep the same project identity for its volumes; never use `down -v` on retained data.

Only Live Web is published, on loopback. PostgreSQL and the kernel have no host
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
They write `live/api.log` and `live/kernel.log` independently, each rotating at 10 MiB
with five backups. Read the kernel file with:

```sh
docker compose --env-file /absolute/private/live.env -f deploy/live/compose.yaml exec live tail -n 50 /var/log/northstar/live/kernel.log
```

The bounded asynchronous writer drops operational records when saturated, counts
loss and disk errors, and never falls back to synchronous kernel disk writes.
`northstar check` includes log health; it does not grant or revoke trading authority.
Log retention and CPU/disk contention must be measured on the deployed host. Keep
trading facts in their durable business stores; log files are not an audit ledger.

## Reproducible local acceptance

```sh
uv run --project backend python scripts/check_live_deployment.py --image northstar-quant:local --frontend-image northstar-live-frontend:local
```

The check creates a uniquely named, isolated Compose project with generated test
authentication and an ephemeral loopback port. It verifies installed pages,
runtime identity across Web restart, database failure and kernel disappearance,
then deletes **only its own disposable containers, networks and volumes**.
It never loads SimNow credentials, invokes broker operations or stops the personal
application. This is actual Docker/HTTP acceptance, not a YAML/layout test and not
evidence that a cloud host or real browser session was tested.
