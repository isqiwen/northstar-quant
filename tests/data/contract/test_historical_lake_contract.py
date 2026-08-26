"""历史 Parquet Lake 与 DuckDB 的存储边界契约。"""

from northstar_quant.data.lake import LakeDatasetKind, load_historical_lake_config
from tests.helpers.paths import PROJECT_ROOT


def test_historical_lake_config_covers_every_supported_kind_and_disables_automatic_cleanup():
    config = load_historical_lake_config(PROJECT_ROOT / "configs" / "data" / "historical_lake.yaml")

    assert {policy.kind for policy in config.policies} == set(LakeDatasetKind)
    for policy in config.policies:
        assert policy.partition_columns
        assert policy.available_at_column == "available_at"


def test_lake_and_duckdb_keep_postgresql_and_trading_outside_their_runtime_boundary():
    lake_sources = "\n".join(
        (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        for relative in (
            "src/northstar_quant/data/lake/config.py",
            "src/northstar_quant/data/lake/models.py",
            "src/northstar_quant/data/lake/publisher.py",
            "src/northstar_quant/data/lake/store.py",
            "src/northstar_quant/research/analytics/duckdb.py",
        )
    )

    for forbidden in (
        "foundation.db",
        "sqlalchemy",
        "alembic",
        "trading_execution",
        "portfolio_risk",
        "NORTHSTAR_DATABASE_URL",
    ):
        assert forbidden not in lake_sources
    assert 'duckdb.connect(":memory:")' in lake_sources
    assert "self._lake_store.verify(request.reference)" in lake_sources
