from pathlib import Path

from northstar_quant.common.enums import AssetType, DataFrequency, RebalanceFrequency, StrategyFamily, StrategyOutputType
from northstar_quant.config.settings import get_settings
from northstar_quant.config.trading_profile import (
    get_production_profile_id,
    list_production_profiles,
    list_trading_profiles,
    load_trading_profile,
)
from northstar_quant.data.storage import profile_market_data_path
from northstar_quant.strategies.etf_rotation import US_ETFDailyRotationStrategy
from northstar_quant.strategies.intraday_breakout import IntradayBreakoutStrategy
from northstar_quant.strategies.momentum import MomentumRotationStrategy
from northstar_quant.strategies.registry import (
    build_profile_strategies,
    build_strategy,
    get_strategy_definition,
    list_registered_strategies,
)


def test_list_trading_profiles_contains_default_skeletons():
    profiles = set(list_trading_profiles())

    assert {
        "cn_etf_daily",
        "cn_etf_daily_research12",
        "cn_stock_daily",
        "cn_stock_weekly",
        "cn_stock_intraday_1m",
        "us_etf_daily",
        "us_etf_daily_research12",
        "us_stock_daily",
        "us_stock_weekly",
        "us_stock_intraday_1m",
    }.issubset(profiles)


def test_list_production_profiles_only_returns_main_money_line():
    assert list_production_profiles() == ["cn_etf_daily"]
    assert get_production_profile_id() == "cn_etf_daily"


def test_load_trading_profile_reads_profile_yaml():
    profile = load_trading_profile("cn_etf_daily")

    assert profile.profile_id == "cn_etf_daily"
    assert profile.market == "CN"
    assert profile.asset_type == AssetType.ETF
    assert profile.data_frequency == DataFrequency.D1
    assert profile.rebalance_frequency == RebalanceFrequency.D1
    assert profile.strategy_family == StrategyFamily.MOMENTUM_ROTATION
    assert profile.currency == "CNY"
    assert profile.timezone == "Asia/Shanghai"
    assert profile.calendar == "XSHG"
    assert profile.benchmark_symbol == "510300.SS"
    assert profile.dimension_key == "cn::etf::1d::1d::momentum_rotation"
    assert profile.data.path == "cn/etf/1d/core.parquet"
    assert profile.data.price_field == "adjusted_close"
    assert profile.is_production is True
    assert profile.lifecycle.role == "production"
    assert profile.lifecycle.line_id == "cn_core_long_only"
    assert profile.execution.long_only is True
    assert profile.execution.rebalance_min_trade_value == 10000.0
    assert profile.execution.rebalance_weight_tolerance == 0.015
    assert profile.versions.profile == "prod-cn-core-v1"
    assert profile.versions.execution_policy == "cn-low-turnover-band-150bps-v1"
    assert [item.strategy_id for item in profile.enabled_strategies] == ["etf_rotation", "momentum"]
    assert [item.strategy_family for item in profile.enabled_strategies] == [
        StrategyFamily.MOMENTUM_ROTATION,
        StrategyFamily.CROSS_SECTIONAL_SELECTION,
    ]
    assert [item.capital_weight for item in profile.enabled_strategies] == [0.7, 0.3]


def test_load_research12_profile_reads_yfinance_download_config():
    profile = load_trading_profile("cn_etf_daily_research12")

    assert profile.profile_id == "cn_etf_daily_research12"
    assert profile.is_production is False
    assert profile.lifecycle.role == "research"
    assert profile.data.provider == "local"
    assert profile.data.download.provider == "yfinance"
    assert profile.data.download.start_date == "2015-01-05"
    assert len(profile.data.download.symbols) == 12
    assert profile.data.download.symbols[0] == "510300.SS"
    assert profile.data.path == "cn/etf/1d/research12.parquet"
    assert profile.data.price_field == "adjusted_close"
    assert profile.currency == "CNY"


def test_profile_market_data_path_is_root_anchored():
    settings = get_settings()
    path = profile_market_data_path("cn_etf_daily")

    assert path.is_absolute()
    assert path == settings.storage_dir / "market" / Path("cn/etf/1d/core.parquet")


def test_strategy_registry_builds_registered_strategies_with_yaml_defaults():
    registered = set(list_registered_strategies())

    assert {"etf_rotation", "momentum", "intraday_breakout"}.issubset(registered)
    assert get_strategy_definition("etf_rotation").strategy_family == StrategyFamily.MOMENTUM_ROTATION
    assert (
        get_strategy_definition("momentum").strategy_family
        == StrategyFamily.CROSS_SECTIONAL_SELECTION
    )
    assert (
        get_strategy_definition("intraday_breakout").strategy_family
        == StrategyFamily.INTRADAY_BREAKOUT
    )
    assert get_strategy_definition("intraday_breakout").output_type == StrategyOutputType.EXECUTION_INTENT

    etf_strategy = build_strategy("etf_rotation")
    momentum_strategy = build_strategy("momentum")
    intraday_strategy = build_strategy("intraday_breakout")

    assert isinstance(etf_strategy, US_ETFDailyRotationStrategy)
    assert etf_strategy.lookback_days == 126
    assert etf_strategy.top_n == 3

    assert isinstance(momentum_strategy, MomentumRotationStrategy)
    assert momentum_strategy.lookback_days == 90
    assert momentum_strategy.top_n == 3

    assert isinstance(intraday_strategy, IntradayBreakoutStrategy)
    assert intraday_strategy.output_type == StrategyOutputType.EXECUTION_INTENT
    assert intraday_strategy.lookback_bars == 30
    assert intraday_strategy.top_n == 2


def test_build_strategy_allows_profile_level_param_override():
    strategy = build_strategy("etf_rotation", params={"lookback_days": 63, "top_n": 2})

    assert isinstance(strategy, US_ETFDailyRotationStrategy)
    assert strategy.lookback_days == 63
    assert strategy.top_n == 2


def test_build_profile_strategies_uses_enabled_profile_entries():
    profile = load_trading_profile("cn_etf_daily")

    built = build_profile_strategies(profile)

    assert [strategy.strategy_id for strategy, _ in built] == ["etf_rotation", "momentum"]
    assert [weight for _, weight in built] == [0.7, 0.3]


def test_build_profile_strategies_supports_intraday_profile():
    profile = load_trading_profile("cn_stock_intraday_1m")

    built = build_profile_strategies(profile)

    assert [strategy.strategy_id for strategy, _ in built] == ["intraday_breakout"]
    assert [weight for _, weight in built] == [1.0]
