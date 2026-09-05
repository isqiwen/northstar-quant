"""One paused, file-driven Paper account with transactional, recoverable steps.

The accepted snapshot fixes the input sequence before any worker can advance it.
Every explicit command consumes at most one next input under the account row lock.
No background loop, broker connection or live execution permission exists here.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import re
from dataclasses import asdict
from datetime import UTC
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Column,
    Connection,
    DateTime,
    Engine,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.engine import RowMapping

from northstar_quant.accounting import Account, FillFact
from northstar_quant.data.catalog.models import DatasetSnapshotManifest
from northstar_quant.data.library import DataLibrary
from northstar_quant.data.research import ResearchBar, ResearchDataset
from northstar_quant.research import ResearchConfig, TradingSession
from northstar_quant.runtime import implementation_hash
from northstar_quant.strategy import decimal_text

_metadata = MetaData()
_configurations = Table(
    "paper_configurations",
    _metadata,
    Column("configuration_id", String(64), primary_key=True),
    Column("name", String(80), nullable=False),
    Column("config", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
_sessions = Table(
    "paper_sessions",
    _metadata,
    Column("session_id", PgUUID(as_uuid=True), primary_key=True),
    Column("configuration_id", ForeignKey(_configurations.c.configuration_id), nullable=False),
    Column("snapshot_id", ForeignKey(DatasetSnapshotManifest.id), nullable=False),
    Column("snapshot_hash", String(64), nullable=False),
    Column("implementation_hash", String(64), nullable=False),
    Column("identity_hash", String(64), nullable=False),
    Column("market", JSONB, nullable=False),
    Column("data", JSONB, nullable=False),
    Column("total_inputs", Integer, nullable=False),
    Column("cursor", Integer, nullable=False),
    Column("checkpoint", JSONB, nullable=False),
    Column("summary", JSONB, nullable=False),
    Column("state_hash", String(64), nullable=False),
    Column("journal_hash", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("total_inputs BETWEEN 1 AND 1440 AND cursor BETWEEN 0 AND total_inputs"),
)
_inputs = Table(
    "paper_inputs",
    _metadata,
    Column("session_id", ForeignKey(_sessions.c.session_id), primary_key=True),
    Column("sequence", Integer, primary_key=True),
    Column("observation_id", PgUUID(as_uuid=True), nullable=False),
    Column("input_hash", String(64), nullable=False),
    UniqueConstraint("session_id", "observation_id"),
    CheckConstraint("sequence > 0"),
)
_steps = Table(
    "paper_steps",
    _metadata,
    Column("session_id", ForeignKey(_sessions.c.session_id), primary_key=True),
    Column("sequence", Integer, primary_key=True),
    Column("request_id", PgUUID(as_uuid=True), nullable=False),
    Column("observation_id", PgUUID(as_uuid=True), nullable=False),
    Column("fill_id", String(256)),
    Column("input_hash", String(64), nullable=False),
    Column("step", JSONB, nullable=False),
    Column("state_hash", String(64), nullable=False),
    Column("journal_hash", String(64), nullable=False),
    Column("committed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("session_id", "request_id"),
    UniqueConstraint("session_id", "observation_id"),
    UniqueConstraint("session_id", "fill_id"),
    CheckConstraint("sequence > 0"),
)


def initialize_session_store(connection: Connection) -> None:
    """Install current Paper tables only during explicit database initialization."""

    if connection.dialect.name != "postgresql":
        raise ValueError("persistent Paper requires PostgreSQL")
    _metadata.create_all(connection)
    connection.exec_driver_sql("""
            CREATE OR REPLACE FUNCTION paper_reject_fact_change() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Paper configuration, input and committed steps are immutable';
            END;
            $$ LANGUAGE plpgsql
        """)
    for table in (_configurations, _inputs, _steps):
        connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS immutable ON {table.name}")
        connection.exec_driver_sql(
            f"CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON {table.name} "
            "FOR EACH ROW EXECUTE FUNCTION paper_reject_fact_change()"
        )


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("stored Paper content must be an object")
    return cast(dict[str, object], value)


def _bar_hash(bar: ResearchBar) -> str:
    return _hash(
        {
            key: decimal_text(value) if isinstance(value, Decimal) else str(value)
            for key, value in asdict(bar).items()
        }
    )


def _uuid(value: UUID) -> None:
    if not isinstance(value, UUID):
        raise ValueError("Paper operation requires a UUID identity")


def _configuration(row: RowMapping) -> dict[str, object]:
    config = ResearchConfig.from_mapping(_object(row["config"])).to_dict()
    name = str(row["name"])
    identity = _hash({"name": name, "config": config})
    if identity != row["configuration_id"]:
        raise ValueError("stored configuration no longer matches its immutable identity")
    strategy_fields = {"lookback", "threshold", "target_fraction", "order_lifetime_seconds"}
    risk_fields = {
        "max_lots",
        "max_gross_notional",
        "max_margin_fraction",
        "initial_margin_fraction",
        "max_adverse_price_move_fraction",
        "fee_per_lot",
        "slippage_ticks",
    }
    return {
        "configuration_id": identity,
        "name": name,
        "config": config,
        "strategy_hash": _hash({key: config[key] for key in sorted(strategy_fields)}),
        "risk_hash": _hash({key: config[key] for key in sorted(risk_fields)}),
        "created_at": row["created_at"].astimezone(UTC).isoformat(),
    }


class SessionStore:
    """Persist file inputs, immutable configuration and one isolated Paper account."""

    def __init__(self, engine: Engine, library: DataLibrary) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("persistent Paper requires PostgreSQL")
        self._engine = engine
        self._library = library

    def save_configuration(self, name: str, config: ResearchConfig) -> dict[str, object]:
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError("configuration name must contain 1 to 80 characters")
        name = name.strip()
        content = config.to_dict()
        identity = _hash({"name": name, "config": content})
        with self._engine.begin() as connection:
            connection.execute(
                insert(_configurations)
                .values(configuration_id=identity, name=name, config=content)
                .on_conflict_do_nothing(index_elements=[_configurations.c.configuration_id])
            )
            return self._get_configuration(connection, identity)

    def list_configurations(self) -> list[dict[str, object]]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    select(_configurations)
                    .order_by(
                        _configurations.c.created_at.desc(), _configurations.c.configuration_id
                    )
                    .limit(100)
                )
                .mappings()
                .all()
            )
            return [_configuration(row) for row in rows]

    def create(
        self, snapshot_id: UUID, configuration_id: str, *, request_id: UUID
    ) -> dict[str, object]:
        _uuid(snapshot_id)
        _uuid(request_id)
        dataset = self._library.load_dataset(snapshot_id)
        if dataset.details is None or not 1 <= len(dataset.bars) <= 1440:
            raise ValueError("Paper requires a verified single DAY snapshot of at most 1440 bars")
        bars = _ordered_bars(dataset)
        if len({bar.trading_day for bar in bars}) != 1 or dataset.market.interval_seconds != 60:
            raise ValueError(
                "Paper currently supports one trading day of minute bars; no settlement"
            )
        implementation = implementation_hash()
        with self._engine.begin() as connection:
            saved_config = self._get_configuration(connection, configuration_id)
            config = ResearchConfig.from_mapping(_object(saved_config["config"]))
            if len(bars) <= config.lookback:
                raise ValueError("Paper requires more bars than the configured lookback")
            session = TradingSession(
                dataset.market,
                config,
                snapshot_id=snapshot_id,
                content_hash=dataset.content_hash,
                data_details=dataset.details,
            )
            market = {
                key: decimal_text(value)
                if isinstance(value, Decimal)
                else str(value)
                if isinstance(value, UUID)
                else value
                for key, value in asdict(dataset.market).items()
            }
            binding = {
                "session_id": str(request_id),
                "configuration_id": configuration_id,
                "snapshot_id": str(snapshot_id),
                "snapshot_hash": dataset.content_hash,
                "implementation_hash": implementation,
                "market": market,
                "data": dataset.details.to_dict(),
                "total_inputs": len(bars),
            }
            identity = _hash(binding)
            checkpoint, summary = session.checkpoint(), session.summary()
            state_hash = _hash(
                {"identity": identity, "cursor": 0, "checkpoint": checkpoint, "summary": summary}
            )
            created = connection.execute(
                insert(_sessions)
                .values(
                    session_id=request_id,
                    configuration_id=configuration_id,
                    snapshot_id=snapshot_id,
                    snapshot_hash=dataset.content_hash,
                    implementation_hash=implementation,
                    identity_hash=identity,
                    market=market,
                    data=dataset.details.to_dict(),
                    total_inputs=len(bars),
                    cursor=0,
                    checkpoint=checkpoint,
                    summary=summary,
                    state_hash=state_hash,
                    journal_hash=identity,
                )
                .on_conflict_do_nothing(index_elements=[_sessions.c.session_id])
                .returning(_sessions.c.session_id)
            ).scalar_one_or_none()
            if created is None:
                prior = connection.execute(
                    select(_sessions.c.identity_hash).where(_sessions.c.session_id == request_id)
                ).scalar_one()
                if prior != identity:
                    raise ValueError("creation request identity was reused with different inputs")
            else:
                connection.execute(
                    insert(_inputs),
                    [
                        {
                            "session_id": request_id,
                            "sequence": index,
                            "observation_id": bar.observation_id,
                            "input_hash": _bar_hash(bar),
                        }
                        for index, bar in enumerate(bars, 1)
                    ],
                )
        return self.get(request_id)

    def list(self) -> list[dict[str, object]]:
        """List bounded summaries; full journal verification belongs to opening/advancing."""

        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                rows = (
                    connection.execute(
                        select(_sessions).order_by(_sessions.c.created_at.desc()).limit(50)
                    )
                    .mappings()
                    .all()
                )
                configurations = {
                    row["configuration_id"]: _configuration(row)
                    for row in connection.execute(
                        select(_configurations).where(
                            _configurations.c.configuration_id.in_(
                                {row["configuration_id"] for row in rows}
                            )
                        )
                    ).mappings()
                }
                return [
                    {
                        "session_id": str(row["session_id"]),
                        "mode": "paper",
                        "input_type": "FILE_REPLAY",
                        "status": "COMPLETED" if row["cursor"] == row["total_inputs"] else "PAUSED",
                        "created_at": row["created_at"].astimezone(UTC).isoformat(),
                        "updated_at": row["updated_at"].astimezone(UTC).isoformat(),
                        "market": row["market"],
                        "summary": row["summary"],
                        "configuration": configurations[row["configuration_id"]],
                        "cursor": row["cursor"],
                        "total_inputs": row["total_inputs"],
                    }
                    for row in (self._check_row(item) for item in rows)
                ]

    def get(self, session_id: UUID) -> dict[str, object]:
        _uuid(session_id)
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                row = self._row(connection, session_id)
                config = self._get_configuration(connection, str(row["configuration_id"]))
                steps = self._journal(connection, row)
                point = None if not steps else _object(steps[-1]["step"])["point"]
                same_implementation = row["implementation_hash"] == implementation_hash()
                completed = row["cursor"] == row["total_inputs"]
                return {
                    "session_id": str(session_id),
                    "account_id": str(session_id),
                    "mode": "paper",
                    "input_type": "FILE_REPLAY",
                    "status": "COMPLETED" if completed else "PAUSED",
                    "created_at": row["created_at"].astimezone(UTC).isoformat(),
                    "updated_at": row["updated_at"].astimezone(UTC).isoformat(),
                    "snapshot": {
                        "id": str(row["snapshot_id"]),
                        "content_hash": row["snapshot_hash"],
                    },
                    "market": row["market"],
                    "data": row["data"],
                    "configuration": config,
                    "implementation_hash": row["implementation_hash"],
                    "cursor": row["cursor"],
                    "total_inputs": row["total_inputs"],
                    "remaining_inputs": row["total_inputs"] - row["cursor"],
                    "last_event": point,
                    "summary": row["summary"],
                    "pending_order": _object(row["checkpoint"])["pending"],
                    "equity_curve": [_object(step["step"])["point"] for step in steps],
                    "decisions": [
                        _object(step["step"])["decision"]
                        for step in steps
                        if _object(step["step"])["decision"] is not None
                    ],
                    "fills": [
                        _object(step["step"])["fill"]
                        for step in steps
                        if _object(step["step"])["fill"] is not None
                    ],
                    "can_advance": same_implementation and not completed,
                    "blocked_reason": None
                    if same_implementation
                    else "实现已改变；仅可查看历史事实，不能用新实现继续旧会话。",
                    "limitations": [
                        "内部 Paper · 已接受文件逐条回放，不是实时行情、柜台仿真或实盘。",
                        "每次明确操作先核对账本，再推进一条输入；操作后暂停，重启不自动推进。",
                        "一个独立模拟账户、单合约、单日盘时段；没有结算、部分撮合或外部委托。",
                        "输入耗尽不代表空仓；残余仓位与待成交授权继续显示。",
                        "策略、费用、滑点和保证金均为固定模拟假设，不证明盈利或授予交易权限。",
                        *cast(list[str], _object(row["data"])["limitations"]),
                    ],
                }

    def advance(self, session_id: UUID, *, request_id: UUID) -> dict[str, object]:
        """Reconcile and consume the next accepted input, atomically and idempotently."""

        _uuid(session_id)
        _uuid(request_id)
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                prior = self._row(connection, session_id)
                recorded = connection.execute(
                    select(_steps.c.request_id).where(
                        _steps.c.session_id == session_id, _steps.c.request_id == request_id
                    )
                ).scalar_one_or_none()
                if recorded is not None:
                    recorded_step = next(
                        item
                        for item in self._journal(connection, prior)
                        if item["request_id"] == request_id
                    )
                    return _step_response(
                        session_id, recorded_step["sequence"], _object(recorded_step["step"])
                    )
                snapshot_id = prior["snapshot_id"]
        dataset = self._library.load_dataset(snapshot_id)
        bars = _ordered_bars(dataset)
        with self._engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            row = self._row(connection, session_id, lock=True)
            steps = self._journal(connection, row)
            existing = next((step for step in steps if step["request_id"] == request_id), None)
            if existing is not None:
                return _step_response(session_id, existing["sequence"], _object(existing["step"]))
            if row["implementation_hash"] != implementation_hash():
                raise ValueError("continuing Paper requires its exact implementation identity")
            if row["cursor"] >= row["total_inputs"]:
                raise ValueError(
                    "all accepted inputs are consumed; residual positions remain visible"
                )
            if dataset.content_hash != row["snapshot_hash"] or len(bars) != row["total_inputs"]:
                raise ValueError("Paper input no longer matches its fixed snapshot")
            saved_config = self._get_configuration(connection, str(row["configuration_id"]))
            config = ResearchConfig.from_mapping(_object(saved_config["config"]))
            account = Account(config.initial_cash, dataset.market)
            for committed in steps:
                fill = _object(committed["step"])["fill"]
                if fill is not None:
                    applied = account.apply(FillFact.from_dict(_object(fill)))
                    if applied.to_dict() != fill:
                        raise ValueError(
                            "Paper ledger does not reproduce its recorded account facts"
                        )
            session = TradingSession.from_checkpoint(
                dataset.market,
                config,
                snapshot_id=dataset.snapshot_id,
                content_hash=dataset.content_hash,
                data_details=dataset.details,
                checkpoint=_object(row["checkpoint"]),
                account=account,
            )
            if session.summary() != row["summary"]:
                raise ValueError("Paper summary does not match its reconciled account state")
            sequence = row["cursor"] + 1
            accepted = (
                connection.execute(
                    select(_inputs).where(
                        _inputs.c.session_id == session_id, _inputs.c.sequence == sequence
                    )
                )
                .mappings()
                .one()
            )
            bar = bars[sequence - 1]
            if accepted["observation_id"] != bar.observation_id or accepted[
                "input_hash"
            ] != _bar_hash(bar):
                raise ValueError("accepted Paper input identity conflicts with snapshot facts")
            result = session.advance(bar)
            if result is None:
                raise ValueError("next unprocessed Paper input was already in its checkpoint")
            step = result.to_dict()
            checkpoint, summary = session.checkpoint(), session.summary()
            state_hash = _hash(
                {
                    "identity": row["identity_hash"],
                    "cursor": sequence,
                    "checkpoint": checkpoint,
                    "summary": summary,
                }
            )
            journal_hash = _hash(
                {
                    "previous": row["journal_hash"],
                    "sequence": sequence,
                    "request_id": str(request_id),
                    "input_hash": accepted["input_hash"],
                    "step": step,
                    "state_hash": state_hash,
                }
            )
            fill = step["fill"]
            connection.execute(
                insert(_steps).values(
                    session_id=session_id,
                    sequence=sequence,
                    request_id=request_id,
                    observation_id=bar.observation_id,
                    fill_id=None if fill is None else _object(fill)["fill_id"],
                    input_hash=accepted["input_hash"],
                    step=step,
                    state_hash=state_hash,
                    journal_hash=journal_hash,
                )
            )
            connection.execute(
                update(_sessions)
                .where(_sessions.c.session_id == session_id)
                .values(
                    cursor=sequence,
                    checkpoint=checkpoint,
                    summary=summary,
                    state_hash=state_hash,
                    journal_hash=journal_hash,
                    updated_at=func.now(),
                )
            )
            return _step_response(session_id, sequence, step)

    @staticmethod
    def _get_configuration(connection: Connection, identity: str) -> dict[str, object]:
        if not isinstance(identity, str) or re.fullmatch(r"[0-9a-f]{64}", identity) is None:
            raise ValueError("configuration_id must be a lowercase SHA-256 identity")
        row = (
            connection.execute(
                select(_configurations).where(_configurations.c.configuration_id == identity)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise LookupError("Paper configuration not found")
        return _configuration(row)

    @staticmethod
    def _row(connection: Connection, identity: UUID, *, lock: bool = False) -> RowMapping:
        query = select(_sessions).where(_sessions.c.session_id == identity)
        row = (
            connection.execute(query.with_for_update() if lock else query).mappings().one_or_none()
        )
        if row is None:
            raise LookupError("Paper session not found")
        return SessionStore._check_row(row)

    @staticmethod
    def _check_row(row: RowMapping) -> RowMapping:
        binding = {
            "session_id": str(row["session_id"]),
            "configuration_id": row["configuration_id"],
            "snapshot_id": str(row["snapshot_id"]),
            "snapshot_hash": row["snapshot_hash"],
            "implementation_hash": row["implementation_hash"],
            "market": row["market"],
            "data": row["data"],
            "total_inputs": row["total_inputs"],
        }
        if (
            _hash(binding) != row["identity_hash"]
            or _hash(
                {
                    "identity": row["identity_hash"],
                    "cursor": row["cursor"],
                    "checkpoint": row["checkpoint"],
                    "summary": row["summary"],
                }
            )
            != row["state_hash"]
        ):
            raise ValueError("Paper binding or checkpoint integrity check failed")
        return row

    @staticmethod
    def _journal(connection: Connection, row: RowMapping) -> builtins.list[RowMapping]:
        rows = list(
            connection.execute(
                select(_steps)
                .where(_steps.c.session_id == row["session_id"])
                .order_by(_steps.c.sequence)
            )
            .mappings()
            .all()
        )
        previous = row["identity_hash"]
        checkpoint = _object(row["checkpoint"])
        if (
            len(rows) != row["cursor"]
            or checkpoint["bar_count"] != row["cursor"]
            or _object(row["summary"])["bar_count"] != row["cursor"]
        ):
            raise ValueError("Paper cursor disagrees with committed steps")
        for sequence, step in enumerate(rows, 1):
            expected = _hash(
                {
                    "previous": previous,
                    "sequence": sequence,
                    "request_id": str(step["request_id"]),
                    "input_hash": step["input_hash"],
                    "step": step["step"],
                    "state_hash": step["state_hash"],
                }
            )
            point = _object(_object(step["step"])["point"])
            if (
                step["sequence"] != sequence
                or expected != step["journal_hash"]
                or point["observation_id"] != str(step["observation_id"])
            ):
                raise ValueError("Paper committed step identity or ordering is inconsistent")
            previous = expected
        if previous != row["journal_hash"] or rows and rows[-1]["state_hash"] != row["state_hash"]:
            raise ValueError("Paper checkpoint does not belong to the committed journal")
        if rows:
            latest = _object(rows[-1]["step"])
            last = _object(checkpoint["last"])
            point = _object(latest["point"])
            if (
                last["observation_id"] != point["observation_id"]
                or last["available_at"] != point["at"]
                or last["close"] != point["close"]
                or checkpoint["pending"] != latest["new_order"]
            ):
                raise ValueError("Paper checkpoint differs from its last committed decision")
        elif checkpoint["last"] is not None or checkpoint["pending"] is not None:
            raise ValueError("empty Paper journal cannot have an observation or pending order")
        return rows


def _ordered_bars(dataset: ResearchDataset) -> tuple[ResearchBar, ...]:
    return tuple(
        sorted(
            dataset.bars,
            key=lambda bar: (bar.available_at, bar.completed_at, str(bar.observation_id)),
        )
    )


def _step_response(session_id: UUID, sequence: int, step: dict[str, object]) -> dict[str, object]:
    return {
        "session_id": str(session_id),
        "sequence": sequence,
        "step": step,
        "url": f"/paper/{session_id}",
    }
