from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from northstar_quant.backtest.registry import (
    list_target_backtesters,
    resolve_target_backtester,
)
from northstar_quant.backtest.runner import run_profile_backtest
from northstar_quant.common.enums import AssetType
from northstar_quant.config.trading_profile import (
    ensure_broker_profile,
    ensure_production_profile,
    list_trading_profiles,
    load_trading_profile,
)
from northstar_quant.config.settings import get_settings
from northstar_quant.data import storage
from northstar_quant.data import downloader
from northstar_quant.data.downloader import (
    download_profile_data,
    import_profile_data,
    validate_profile_data,
)
from northstar_quant.data.storage import (
    load_json,
    load_profile_signal_data,
    save_parquet,
)
from tests.support.futures_actual import (
    actual_futures_frame,
    actual_futures_intraday_frame,
)


def test_runtime_exposes_continuous_and_actual_futures_research_profiles():
    profile = load_trading_profile()

    assert list_trading_profiles() == [
        "cn_futures_daily_actual_offline",
        "cn_futures_daily_trend_offline",
        "cn_futures_daily_trend_simulated",
        "cn_futures_intraday_replay_offline",
    ]
    assert profile.asset_type == AssetType.FUTURES
    assert profile.futures is not None
    assert profile.futures.symbols_are_continuous is True
    assert profile.futures.execution_allowed is False

    actual = load_trading_profile("cn_futures_daily_actual_offline")
    assert actual.backtest.engine == "futures_daily"
    assert actual.futures is not None
    assert actual.futures.symbols_are_continuous is False
    assert actual.futures.execution_allowed is False
    assert actual.data.download.enabled is True
    assert actual.data.download.provider == "akshare_actual_daily"

    intraday = load_trading_profile("cn_futures_intraday_replay_offline")
    assert intraday.backtest.engine == "futures_intraday_replay"
    assert intraday.data_frequency.value == "1m"
    assert intraday.strategy_data_frequency.value == "1d"
    assert list_target_backtesters() == [
        "actual_futures_daily_backtest",
        "actual_futures_intraday_replay_backtest",
        "continuous_futures_research_backtest",
    ]
    assert (
        resolve_target_backtester(intraday).backtester_id
        == "actual_futures_intraday_replay_backtest"
    )


def _download_fixture_data() -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    start = date(2023, 1, 3)
    for symbol_index, symbol in enumerate(
        (
            "RB_CONT",
            "CU_CONT",
            "I_CONT",
            "M_CONT",
            "TA_CONT",
            "SA_CONT",
            "SI_CONT",
            "SC_CONT",
        )
    ):
        for offset in range(90):
            close = 3000.0 + symbol_index * 100 + offset * (1.0 + symbol_index * 0.1)
            rows.append(
                {
                    "date": start + timedelta(days=offset),
                    "symbol": symbol,
                    "open": close - 5,
                    "high": close + 10,
                    "low": close - 10,
                    "close": close,
                    "adjusted_close": close,
                    "volume": 100000.0,
                }
            )
    return pl.DataFrame(rows)


def test_continuous_futures_profile_can_run_research_but_not_execution(monkeypatch, tmp_path: Path):
    """下载集成测试必须使用临时 storage，绝不写入用户的真实研究数据目录。"""

    isolated_storage = tmp_path / "storage"
    isolated_settings = get_settings().model_copy(
        update={
            "storage_dir": isolated_storage,
            "downloads_dir": isolated_storage / "downloads",
        }
    )
    monkeypatch.setattr(storage, "get_settings", lambda: isolated_settings)
    monkeypatch.setitem(downloader._PROVIDERS, "akshare", lambda _profile: _download_fixture_data())

    result = download_profile_data()
    validation = validate_profile_data()
    backtest = run_profile_backtest()

    assert result.symbol_count == 8
    assert Path(result.dataset_path).is_relative_to(isolated_storage)
    assert len(result.symbol_quality) == 8
    assert result.symbol_quality[0]["row_count"] == 90
    assert validation["status"] == "ok"
    assert backtest["selected_strategy_ids"] == ["futures_trend"]
    assert {row["symbol"] for row in backtest["latest_holdings"]} == {
        "RB_CONT",
        "CU_CONT",
        "I_CONT",
        "M_CONT",
        "TA_CONT",
        "SA_CONT",
        "SI_CONT",
        "SC_CONT",
    }
    manifest = load_json(Path(result.dataset_manifest_path))
    assert manifest["manifest_version"] == "data_manifest_v2"
    assert len(manifest["content_sha256"]) == 64
    assert len(manifest["profile_config_sha256"]) == 64


def test_actual_contract_profile_can_download_and_run_full_backtest(
    monkeypatch,
    tmp_path: Path,
):
    isolated_storage = tmp_path / "storage"
    isolated_settings = get_settings().model_copy(
        update={
            "storage_dir": isolated_storage,
            "downloads_dir": isolated_storage / "downloads",
        }
    )
    monkeypatch.setattr(storage, "get_settings", lambda: isolated_settings)
    monkeypatch.setitem(
        downloader._PROVIDERS,
        "akshare_actual_daily",
        lambda _profile: actual_futures_frame(day_count=70, roll_offset=35),
    )

    downloaded = download_profile_data("cn_futures_daily_actual_offline")
    validation = validate_profile_data("cn_futures_daily_actual_offline")
    backtest = run_profile_backtest("cn_futures_daily_actual_offline")

    assert downloaded.schema_version == "actual_futures_daily_v1"
    assert validation["no_lookahead_active_contracts"] is True
    assert backtest["trade_count"] > 0
    assert backtest["symbols"] == ["RB_CONT"]


def test_intraday_contract_profile_can_import_and_run_full_replay(
    monkeypatch,
    tmp_path: Path,
):
    isolated_storage = tmp_path / "storage"
    isolated_settings = get_settings().model_copy(
        update={
            "storage_dir": isolated_storage,
            "downloads_dir": isolated_storage / "downloads",
        }
    )
    monkeypatch.setattr(storage, "get_settings", lambda: isolated_settings)
    source_path = tmp_path / "actual_contracts_intraday.parquet"
    actual_futures_intraday_frame(day_count=70, roll_offset=35).write_parquet(
        source_path
    )

    imported = import_profile_data(
        source_path,
        "cn_futures_intraday_replay_offline",
    )
    validation = validate_profile_data("cn_futures_intraday_replay_offline")
    backtest = run_profile_backtest("cn_futures_intraday_replay_offline")

    assert imported.schema_version == "actual_futures_intraday_v1"
    assert validation["quote_replay_ready"] is True
    assert backtest["trade_count"] > 0
    assert backtest["symbols"] == ["RB_CONT"]


def test_profile_data_load_rejects_parquet_tampering(monkeypatch, tmp_path: Path):
    isolated_storage = tmp_path / "storage"
    isolated_settings = get_settings().model_copy(
        update={
            "storage_dir": isolated_storage,
            "downloads_dir": isolated_storage / "downloads",
        }
    )
    monkeypatch.setattr(storage, "get_settings", lambda: isolated_settings)
    monkeypatch.setitem(downloader._PROVIDERS, "akshare", lambda _profile: _download_fixture_data())
    result = download_profile_data()
    tampered = _download_fixture_data().with_columns(
        pl.when(pl.col("symbol") == "RB_CONT")
        .then(pl.col("close") + 100)
        .otherwise(pl.col("close"))
        .alias("close")
    )
    save_parquet(tampered, result.dataset_path)

    with pytest.raises(ValueError, match="内容哈希"):
        load_profile_signal_data()


def test_continuous_futures_profile_is_rejected_for_production_execution():
    profile = replace(load_trading_profile(), lifecycle=replace(load_trading_profile().lifecycle, role="production"))

    with pytest.raises(ValueError, match="连续合约"):
        ensure_production_profile(profile, context="测试")


def test_ctp_sim_requires_simulated_executable_profile():
    simulated = load_trading_profile("cn_futures_daily_trend_simulated")
    research = load_trading_profile("cn_futures_daily_actual_offline")

    assert (
        ensure_broker_profile(
            simulated,
            broker="ctp_sim",
            context="测试",
        )
        is simulated
    )
    with pytest.raises(ValueError, match="仅允许 simulated 画像"):
        ensure_broker_profile(research, broker="ctp_sim", context="测试")
    with pytest.raises(ValueError, match="仅允许使用 production 画像"):
        ensure_broker_profile(simulated, broker="ctp", context="测试")


def test_futures_profile_requires_contract_specification(tmp_path: Path):
    path = tmp_path / "offline" / "invalid_futures_offline.yaml"
    path.parent.mkdir()
    path.write_text(
        """
profile_id: invalid_futures_offline
market: CN
asset_type: FUTURES
lifecycle:
  role: research
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="必须配置 futures.contract_spec_path"):
        load_trading_profile("invalid_futures_offline", tmp_path)


def _write_boolean_profile(tmp_path: Path, *, boolean_value: str) -> str:
    profile_id = f"strict_bool_{boolean_value}_offline"
    path = tmp_path / "offline" / f"{profile_id}.yaml"
    path.parent.mkdir()
    path.write_text(
        f"""
profile_id: {profile_id}
market: CN
asset_type: FUTURES
data_frequency: 1d
rebalance_frequency: 1d
strategy_family: trend_following
currency: CNY
timezone: Asia/Shanghai
calendar: XSHG
universe_id: test
benchmark_symbol: RB_CONT
lifecycle:
  role: research
futures:
  contract_spec_path: configs/futures/test.yaml
  ctp_contract_mapping_path: configs/instruments/test.yaml
  symbols_are_continuous: true
  execution_allowed: false
execution:
  long_only: "{boolean_value}"
data:
  adjusted: "{boolean_value}"
  live_trading_eligible: false
  download:
    enabled: "{boolean_value}"
strategies:
  - strategy_id: futures_trend
    enabled: "{boolean_value}"
""".strip(),
        encoding="utf-8",
    )
    return profile_id


def test_profile_boolean_strings_are_parsed_by_value(tmp_path: Path):
    profile_id = _write_boolean_profile(tmp_path, boolean_value="false")

    profile = load_trading_profile(profile_id, tmp_path)

    assert profile.execution.long_only is False
    assert profile.data.adjusted is False
    assert profile.data.download.enabled is False
    assert profile.strategies[0].enabled is False


def test_profile_rejects_ambiguous_boolean_strings(tmp_path: Path):
    profile_id = _write_boolean_profile(tmp_path, boolean_value="maybe")

    with pytest.raises(ValueError, match="必须是明确的布尔值"):
        load_trading_profile(profile_id, tmp_path)
