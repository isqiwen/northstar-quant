from pathlib import Path

from northstar_quant.common.enums import AssetType, DataFrequency, RebalanceFrequency, StrategyFamily, StrategyOutputType
from northstar_quant.config.settings import get_settings
from northstar_quant.config.trading_profile import load_trading_profile, list_trading_profiles
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
        "us_etf_daily",
        "us_etf_daily_research12",
        "us_stock_daily",
        "us_stock_weekly",
        "us_stock_intraday_1m",
    }.issubset(profiles)


def test_load_trading_profile_reads_profile_yaml():
    profile = load_trading_profile("us_etf_daily")

    assert profile.profile_id == "us_etf_daily"
    assert profile.market == "US"
    assert profile.asset_type == AssetType.ETF
    assert profile.data_frequency == DataFrequency.D1
    assert profile.rebalance_frequency == RebalanceFrequency.D1
    assert profile.strategy_family == StrategyFamily.MOMENTUM_ROTATION
    assert profile.currency == "USD"
    assert profile.dimension_key == "us::etf::1d::1d::momentum_rotation"
    assert profile.data.path == "us/etf/1d/core.parquet"
    assert profile.data.price_field == "adjusted_close"
    assert [item.strategy_id for item in profile.enabled_strategies] == ["etf_rotation", "momentum"]
    assert [item.strategy_family for item in profile.enabled_strategies] == [
        StrategyFamily.MOMENTUM_ROTATION,
        StrategyFamily.CROSS_SECTIONAL_SELECTION,
    ]
    assert [item.capital_weight for item in profile.enabled_strategies] == [0.7, 0.3]


def test_load_research12_profile_reads_yfinance_download_config():
    profile = load_trading_profile("us_etf_daily_research12")

    assert profile.profile_id == "us_etf_daily_research12"
    assert profile.data.provider == "local"
    assert profile.data.download.provider == "yfinance"
    assert profile.data.download.start_date == "2005-01-03"
    assert len(profile.data.download.symbols) == 12
    assert profile.data.download.symbols[0] == "SPY"
    assert profile.data.path == "us/etf/1d/research12.parquet"
    assert profile.data.price_field == "adjusted_close"
    assert profile.currency == "USD"


def test_profile_market_data_path_is_root_anchored():
    settings = get_settings()
    path = profile_market_data_path("us_etf_daily")

    assert path.is_absolute()
    assert path == settings.storage_dir / "market" / Path("us/etf/1d/core.parquet")


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
    profile = load_trading_profile("us_etf_daily")

    built = build_profile_strategies(profile)

    assert [strategy.strategy_id for strategy, _ in built] == ["etf_rotation", "momentum"]
    assert [weight for _, weight in built] == [0.7, 0.3]


def test_build_profile_strategies_supports_intraday_profile():
    profile = load_trading_profile("us_stock_intraday_1m")

    built = build_profile_strategies(profile)

    assert [strategy.strategy_id for strategy, _ in built] == ["intraday_breakout"]
    assert [weight for _, weight in built] == [1.0]
