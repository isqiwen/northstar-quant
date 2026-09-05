"""One explicit read-only broker operation, shared by the browser and CLI.

The request identity fixes an evidence collection, not a renewable connection.
Retries return that collection, including an interrupted one. Native calls have
no order/cancel operation and never start from GET, application startup or restore.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Engine, text

from northstar_quant.broker import ctp
from northstar_quant.broker.baselines import BrokerBaselines
from northstar_quant.broker.ledger import BrokerLedger
from northstar_quant.broker.records import BrokerRecords, QueryCapture
from northstar_quant.broker.settings import (
    credential_status,
    get_profile,
    load_credentials,
    profiles,
    validate_instrument,
)


class BrokerWorkspace:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._records = BrokerRecords(engine)
        self._baselines = BrokerBaselines(engine)
        self._ledger = BrokerLedger(engine)

    @staticmethod
    def status() -> dict[str, object]:
        return {
            "profiles": profiles(),
            "credentials": credential_status(),
            "sdk": ctp.sdk_status(),
            "execution": {"order_sending": False, "cancel_sending": False},
            "connection": "ON_DEMAND_READ_ONLY",
        }

    def list(self, *, limit: int = 50) -> list[dict[str, object]]:
        return self._records.list(limit=limit)

    def get(self, batch_id: UUID) -> dict[str, object]:
        return self._records.get(batch_id)

    def baseline_context(self, query_batch_id: UUID) -> dict[str, object]:
        return self._baselines.context(query_batch_id)

    def establish_baseline(self, source_batch_id: UUID, *, request_id: UUID) -> dict[str, object]:
        """Record an existing observation locally, without credentials or a connection."""

        return self._baselines.establish(source_batch_id, request_id=request_id)

    def compare_baseline(
        self, baseline_id: UUID, query_batch_id: UUID, *, request_id: UUID
    ) -> dict[str, object]:
        return self._baselines.compare(baseline_id, query_batch_id, request_id=request_id)

    def get_baseline_check(self, check_id: UUID) -> dict[str, object]:
        return self._baselines.get_check(check_id)

    def ledger_context(self, query_batch_id: UUID) -> dict[str, object]:
        return self._ledger.context(query_batch_id)

    def ingest_positions(
        self, baseline_id: UUID, source_batch_id: UUID, *, request_id: UUID
    ) -> dict[str, object]:
        """Apply saved external fills locally; never accept operator-supplied positions."""

        return self._ledger.ingest(baseline_id, source_batch_id, request_id=request_id)

    def compare_positions(
        self, entry_id: UUID, query_batch_id: UUID, *, request_id: UUID
    ) -> dict[str, object]:
        return self._ledger.compare(entry_id, query_batch_id, request_id=request_id)

    def get_position_entry(self, entry_id: UUID) -> dict[str, object]:
        return self._ledger.get(entry_id)

    def get_position_check(self, check_id: UUID) -> dict[str, object]:
        return self._ledger.get_check(check_id)

    def check_orders(self, position_check_id: UUID, *, request_id: UUID) -> dict[str, object]:
        """Compare saved order observations with recorded fills without querying again."""

        return self._ledger.check_orders(position_check_id, request_id=request_id)

    def get_order_check(self, check_id: UUID) -> dict[str, object]:
        return self._ledger.get_order_check(check_id)

    def query(self, profile_name: str, instrument: str, *, request_id: UUID) -> dict[str, object]:
        profile = get_profile(profile_name)
        instrument = validate_instrument(instrument)
        try:
            saved = self._records.get(request_id)
        except LookupError:
            pass
        else:
            if saved["profile"] != profile.identity() or saved["instrument"] != instrument:
                raise ValueError("broker request identity is already bound to different input")
            # A receipt fixes the original account. Changing/removing credentials
            # cannot rebind it or prevent reading an uncertain acknowledgement.
            return saved
        credentials = load_credentials()
        scope = f"northstar-simnow-query:{profile.name}:{credentials.user_id}"
        lock_key = int.from_bytes(hashlib.sha256(scope.encode()).digest()[:8], "big", signed=True)
        with self._engine.begin() as connection:
            admitted = connection.execute(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_key}
            ).scalar_one()
            if not admitted:
                raise ValueError("a query for this SimNow environment/account is already running")
            try:
                self._records.get(request_id)
            except LookupError:
                existing = False
            else:
                existing = True
            batch = self._records.begin(
                profile.identity(), credentials.user_id, instrument, request_id=request_id
            )
            if existing:
                return batch
            started = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            try:
                capture = ctp.query_account(profile, credentials, instrument)
            except Exception:
                # Never let a vendor exception serialize credentials or arbitrary
                # broker bytes into logs or HTTP. Partial native failures normally
                # return their bounded event capture, not this last-resort result.
                capture = QueryCapture(
                    started_at=started,
                    finished_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    binding_name="ctpwrapper",
                    binding_version=None,
                    trader_api_version=None,
                    market_api_version=None,
                    events=(),
                    failure_code="ADAPTER_FAILURE",
                )
            return self._records.finish(request_id, capture)
