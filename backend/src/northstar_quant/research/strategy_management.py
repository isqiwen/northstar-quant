"""Research-owned strategy versions and explicit, immutable release candidates."""

from __future__ import annotations

import builtins
import json
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, Connection, Engine, MetaData, String, Table, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert

from northstar_quant import code_revision
from northstar_quant.factors.definition import content_id
from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.research.configurations import ConfigurationStore
from northstar_quant.research.runs import RunStore
from northstar_quant.research.storage import UTCDateTime, write_transaction
from northstar_quant.strategies.artifacts import CANDIDATE_FORMAT, verify_candidate

_metadata = MetaData()
_versions = Table(
    "strategy_versions",
    _metadata,
    Column("version_id", String(64), primary_key=True),
    Column("name", String(80), nullable=False),
    Column("document", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Column("created_at", UTCDateTime(), server_default=func.now(), nullable=False),
)
_candidates = Table(
    "strategy_candidates",
    _metadata,
    Column("candidate_id", String(64), primary_key=True),
    Column("version_id", String(64), nullable=False),
    Column("document", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Column("created_at", UTCDateTime(), server_default=func.now(), nullable=False),
)


def initialize_strategy_management(connection: Connection) -> None:
    _metadata.create_all(connection)
    if connection.dialect.name == "sqlite":
        from .storage import immutable

        for table in ("strategy_versions", "strategy_candidates"):
            immutable(connection, table)
        return
    for table in ("strategy_versions", "strategy_candidates"):
        connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS immutable ON {table}")
        connection.exec_driver_sql(
            f"CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION factor_immutable()"
        )


class StrategyVersions:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def register(self, name: str, configuration_id: str, run_ids: list[str]) -> str:
        if (
            not isinstance(name, str)
            or not 1 <= len(name.strip()) <= 80
            or not 1 <= len(run_ids) <= 10
        ):
            raise ValueError("strategy version needs a name and 1 to 10 fixed research runs")
        configuration = ConfigurationStore(self._engine).get_configuration(configuration_id)
        evidence = [RunStore(self._engine).get(identifier) for identifier in sorted(set(run_ids))]
        if any(item["config"] != configuration["config"] for item in evidence):
            raise ValueError(
                "research does not use this exact strategy, factors, risk "
                "and simulation configuration"
            )
        document = {
            "configuration": configuration,
            "code_revision": code_revision(),
            "evidence": evidence,
            "validation": {
                "status": "BOUNDED_RESEARCH_ONLY",
                "plan": "OBSERVED_COSTS_AND_AVAILABILITY_V1",
                "limitations": [
                    "样本内有界研究，不是样本外检验或盈利保证。",
                    "发布候选不创建 Live 实例，不授予账户权限。",
                ],
            },
        }
        identity = content_id(document)
        with write_transaction(self._engine) as connection:
            connection.execute(
                insert(_versions)
                .values(version_id=identity, name=name.strip(), document=document)
                .on_conflict_do_nothing()
            )
        return identity

    def get(self, version_id: str) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = (
                connection.execute(select(_versions).where(_versions.c.version_id == version_id))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise LookupError("strategy version not found")
        if content_id(row["document"]) != version_id:
            raise ValueError("strategy version integrity failure")
        # Recheck referenced records; a damaged/missing reference is never publishable.
        for run in row["document"]["evidence"]:
            if RunStore(self._engine).get(run["run_id"]) != run:
                raise ValueError("strategy research reference changed")
        return {
            key: item.isoformat() if isinstance(item, datetime) else item
            for key, item in row.items()
        }

    def list(self) -> builtins.list[dict[str, Any]]:
        with self._engine.connect() as connection:
            identities = connection.scalars(
                select(_versions.c.version_id).order_by(_versions.c.created_at.desc()).limit(100)
            ).all()
        return [self.get(identity) for identity in identities]

    def publish(self, version_id: str) -> dict[str, Any]:
        document = self.get(version_id)["document"]
        config = ResearchConfig.from_mapping(document["configuration"]["config"])
        refs = [
            document["code_revision"],
            config.strategy.code_revision,
            *(binding.code_revision for _, binding in config.strategy.factors),
            *(run["code_revision"] for run in document["evidence"]),
        ]
        candidate: dict[str, Any] = {
            "format": CANDIDATE_FORMAT,
            "version_id": version_id,
            "document": document,
            "same_clean_revision": len(set(refs)) == 1 and not refs[0].endswith("-dirty"),
        }
        identity = content_id(candidate)
        candidate["candidate_id"] = identity
        verify_candidate(candidate)
        if len(json.dumps(candidate).encode()) > 4_000_000:
            raise ValueError("candidate exceeds the bounded transfer size")
        with write_transaction(self._engine) as connection:
            connection.execute(
                insert(_candidates)
                .values(candidate_id=identity, version_id=version_id, document=candidate)
                .on_conflict_do_nothing()
            )
        return candidate

    def candidates(self) -> builtins.list[dict[str, Any]]:
        with self._engine.connect() as connection:
            rows = connection.scalars(
                select(_candidates.c.document).order_by(_candidates.c.created_at.desc()).limit(100)
            ).all()
        return [verify_candidate(row) for row in rows]

    def verify_all(self) -> None:
        with self._engine.connect() as connection:
            versions = connection.scalars(select(_versions.c.version_id)).all()
            candidates = connection.scalars(select(_candidates.c.document)).all()
        for version in versions:
            self.get(version)
        for candidate in candidates:
            verify_candidate(candidate)
            if self.get(candidate["version_id"])["document"] != candidate["document"]:
                raise ValueError("candidate version reference mismatch")
