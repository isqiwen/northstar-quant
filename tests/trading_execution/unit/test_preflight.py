"""实盘 preflight 数据血缘门禁测试。"""

from dataclasses import replace
from datetime import UTC, datetime

import polars as pl

from northstar_quant.foundation.config.settings import get_settings
from northstar_quant.foundation.config.trading_profile import load_trading_profile
from northstar_quant.trading_execution.execution.models import BrokerStateSnapshot
from northstar_quant.trading_execution.live.preflight import build_preflight_result


def _provenance_check(*, allowlist: str, manifest: dict):
    settings = get_settings().model_copy(
        update={"approved_live_data_providers": allowlist}
    )
    profile = load_trading_profile()
    profile = replace(
        profile,
        data=replace(profile.data, live_trading_eligible=True),
    )
    checked_at = datetime(2024, 1, 2, 16, 0, tzinfo=UTC)
    frame = pl.DataFrame(
        {
            "date": [datetime(2024, 1, 2, tzinfo=UTC)],
            "symbol": ["RB2405"],
            "close": [100.0],
        }
    )
    result = build_preflight_result(
        profile=profile,
        settings=settings,
        raw_market_df=frame,
        signal_market_df=frame,
        output_frame=pl.DataFrame(
            {
                "date": [datetime(2024, 1, 2, tzinfo=UTC)],
                "symbol": ["RB2405"],
                "target_weight": [0.1],
            }
        ),
        output_time_column="date",
        broker_state=BrokerStateSnapshot(
            account="ctp-account",
            asof=checked_at,
        ),
        execution_symbols=[],
        execution_reference_prices={},
        execution_price_sources={},
        equity=100_000.0,
        broker_name="ctp",
        expected_account="ctp-account",
        data_manifest=manifest,
        checked_at=checked_at,
    )
    return next(check for check in result.checks if check.code == "data_provenance")


def test_preflight_rejects_live_provider_when_allowlist_is_empty():
    check = _provenance_check(
        allowlist="",
        manifest={
            "manifest_version": "data_manifest_v3",
            "profile_id": "cn_futures_daily_trend_offline",
            "dataset_id": "continuous_research",
            "live_trading_eligible": True,
            "data_source": "verified_vendor",
            "content_sha256": "a" * 64,
        },
    )

    assert check.status == "fail"
    assert "未配置真实交易数据提供器白名单" in check.message


def test_preflight_accepts_explicitly_approved_hashed_provider():
    check = _provenance_check(
        allowlist="verified_vendor",
        manifest={
            "manifest_version": "data_manifest_v3",
            "profile_id": "cn_futures_daily_trend_offline",
            "dataset_id": "continuous_research",
            "live_trading_eligible": True,
            "data_source": "verified_vendor",
            "content_sha256": "a" * 64,
        },
    )

    assert check.status == "pass"
