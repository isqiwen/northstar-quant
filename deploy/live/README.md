# Standalone Live deployment

This is the current Live-only topology, not a second implementation. It runs the
same installed Python package as Data Hub and Research, with its own
database, source files and deployment authentication. Next.js, the Python management API and the kernel run independently. It does not start or mount
Data Hub or Research. Do not reuse the personal application's directories or attach
its database network. Never run two instances against one broker account.

This is a **no-broker-credentials engineering baseline**, not cloud or trading
acceptance. Fixed strategy/contract/warm-up material delivery, a selected cloud
host and external alert channel remain #40 work. Startup does not connect a
broker, reconcile an account or authorize orders. Do not attach broker secrets
until the deployment and connection scope are separately approved.

## Fixed host directories

Live uses `/opt/northstar/apps/live` for releases and `/opt/northstar/config/live.env` for configuration.
Persistent database and sources reside in `/opt/northstar/state/live/{postgresql,sources}`;
authentication resides in `/opt/northstar/credentials/live`, and logs in `/opt/northstar/logs/live`.
`northstarctl init-host live` connects as the configured user, elevates via sudo when non-root, and prepares the northstar account, public key and passwordless sudo.
`northstarctl deploy live` then connects as northstar and prepares local directories and permissions.
The initial `live.env` is uploaded automatically from the committed repository. Existing configuration is preserved unless `deploy live --env-file <local-file>` explicitly supplies a replacement; retained data is not overwritten.
Paths are fixed, without environment overrides. All Live state remains on its own host.
No existing named volumes or personal data are migrated automatically.

## Start from an installed image

Use a previously verified Linux amd64 image by registry digest. Local acceptance
may instead use `make up-live` to build from the current source. Compose has build definitions;
use `up --no-build` for deployment of already-verified images. The default password
`123456` is the local development database password. Live containers have no Docker
CPU, memory, swap or process-count limits configured.
The repository maintains defaults in [live/.env](.env). The deployment script uploads it on first deployment as a private owner-only file; keep real passwords out of Git.
northstarctl selects backend and frontend images from the application and Git revision;
image selection is not part of the application .env configuration.
The supported settings are:

- `NORTHSTAR_LIVE_DATABASE_PASSWORD`: the local database password, default `123456`.

Run on the intended host after deployment authorization:

```sh
docker compose --env-file /opt/northstar/config/live.env -f deploy/live/compose.yaml up -d --no-build
docker compose --env-file /opt/northstar/config/live.env -f deploy/live/compose.yaml ps
```

Do not print rendered Compose configuration: it contains the database password.
Changing that variable does not rotate the password of an existing database.
Persistent host paths are fixed; changing the Compose project name does not create a separate Live instance.

Live Web is published on the fixed address `0.0.0.0:18080`, accessible through the host IP
or `live.wangqiwen.me`, without source IP restrictions. Deployment opens its frontend
port; same-origin, browser-session and trading authorization checks remain in place.
The API uses the fixed loopback address `127.0.0.1:19080`. Neither port is configurable. PostgreSQL and the kernel have no host
ports; the frontend and management API have no storage network, database settings,
sources or broker secrets.

Web readiness only means its HTTP process is serving. Query `/api/live/status`
through the normal same-origin session or run `northstar status` inside the
kernel for its actual identity/storage availability. Neither proves tradability.
Web restart is independent; kernel/database restart never restores sending authority.
Automatic container restart is not an external host-loss alert.

## Operational logs

The fixed host directory `/opt/northstar/logs/live` is mounted at `/var/log/northstar/live` in the API and kernel.
They write `live/northstar-live-api-YYYY-MM-DD.log` and
`live/northstar-live-kernel-YYYY-MM-DD.log` independently. The date follows the process
timezone and switches on the first background write after midnight. Files rotate at
10 MiB; each program retains at most five historical files across dates and size
rotations, ordered by modification time. Replace `YYYY-MM-DD` below with the log date:

```sh
docker compose --env-file /opt/northstar/config/live.env -f deploy/live/compose.yaml exec live tail -n 50 /var/log/northstar/live/northstar-live-kernel-YYYY-MM-DD.log
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
then deletes **only its own disposable containers, networks and temporary directories**.
It never loads SimNow credentials, invokes broker operations or stops the personal
application. This is actual Docker/HTTP acceptance, not a YAML/layout test and not
evidence that a cloud host or real browser session was tested.

## Broker environment

Live Sim and future production use this same application and Compose on the Live host.
`NORTHSTAR_LIVE_ENVIRONMENT=simnow_trading` selects SimNow’s first environment (default).
`simnow_dev` selects its API-development environment, which does not provide settlement.
There is no separate NORTHSTAR_SIMNOW_PROFILE setting. Browser/CLI queries use the kernel
environment, not a caller-supplied profile; saved receptions from another environment are rejected.
Production trading is not implemented/admitted: `production` or an unknown value fails kernel
startup before opening its database or reading broker credentials. Configuration never grants sending authority.

Set NORTHSTAR_SIMNOW_USER_ID, NORTHSTAR_SIMNOW_APP_ID, NORTHSTAR_SIMNOW_AUTH_CODE
and NORTHSTAR_SIMNOW_PASSWORD in deploy/live/.env. Keep actual values local and single-quoted
so Compose preserves literal dollar signs. Only the kernel receives these variables;
the frontend, API and database do not. There is no separate broker.env or setup script.
Use northstarctl.py deploy live --env-file deploy/live/.env to update an existing remote
configuration. Empty credentials leave broker setup unconfigured; startup never connects.

Future production admission must bind environment/account, segregate ledger/order/recovery
state and reconcile before explicitly enabling sending. Switching a configuration value must
not reuse simulation account facts as production facts; that transition is not yet supported.
