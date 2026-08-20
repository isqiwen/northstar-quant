"""实时风控结论持久化与逐订单门禁测试。"""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from northstar_quant.platform.common.time import utc_now
from northstar_quant.platform.config.settings import get_settings
from northstar_quant.platform.db.repositories import (
    latest_runtime_risk_record,
    save_runtime_risk_record,
)
from northstar_quant.trading_execution.execution.models import OrderRequest
from northstar_quant.application import live_service as service


def test_submission_guard_uses_latest_persisted_runtime_risk(
    monkeypatch,
    postgresql_session_factory,
):
    now = utc_now()
    with postgresql_session_factory() as session:
        save_runtime_risk_record(
            session,
            profile_id="cn_futures_daily_live",
            broker="ctp",
            account="ctp-test",
            can_submit=True,
            blocking_failure_count=0,
            warning_count=0,
            checks=[],
            checked_at=now,
        )

    settings = get_settings().model_copy(
        update={
            "broker": "ctp",
            "live_trading_enabled": True,
            "kill_switch_enabled": False,
            "runtime_risk_gate_max_age_seconds": 90,
        }
    )
    monkeypatch.setattr(service, "SessionLocal", postgresql_session_factory)
    monkeypatch.setattr(service, "load_settings", lambda: settings)
    monkeypatch.setattr(
        service,
        "load_trading_profile_uncached",
        lambda _profile_id: SimpleNamespace(
            profile_id="cn_futures_daily_live",
            calendar="CN_FUTURES",
            timezone="Asia/Shanghai",
        ),
    )
    monkeypatch.setattr(service, "validate_profile_data_governance", lambda _profile: None)
    monkeypatch.setattr(
        service,
        "ensure_broker_profile",
        lambda profile, **_kwargs: profile,
    )
    calendar_calls: list[dict[str, object]] = []
    contract_calls: list[dict[str, object]] = []
    monkeypatch.setattr(service, "load_calendar_service_for_profile", lambda _profile: object())
    monkeypatch.setattr(
        service,
        "assert_execution_contract_admissible",
        lambda **kwargs: (contract_calls.append(kwargs), (object(), object()))[1],
    )
    monkeypatch.setattr(
        service,
        "assert_order_calendar_open",
        lambda **kwargs: calendar_calls.append(kwargs),
    )
    order = OrderRequest(
        strategy_id="portfolio",
        profile_id="cn_futures_daily_live",
        account="ctp-test",
        symbol="RB2610",
        side="BUY",
        qty=1.0,
    )

    service._assert_live_submission_allowed("ctp", order)

    assert len(calendar_calls) == 1
    assert calendar_calls[0]["order"] is order
    assert len(contract_calls) == 1
    assert contract_calls[0]["order"] is order

    with postgresql_session_factory() as session:
        save_runtime_risk_record(
            session,
            profile_id="cn_futures_daily_live",
            broker="ctp",
            account="ctp-test",
            can_submit=False,
            blocking_failure_count=1,
            warning_count=0,
            checks=[],
            checked_at=now + timedelta(seconds=1),
        )
        latest = latest_runtime_risk_record(
            session,
            profile_id="cn_futures_daily_live",
            broker="ctp",
            account="ctp-test",
        )

    assert latest is not None
    assert latest.can_submit is False
    with pytest.raises(PermissionError, match="RUNTIME_RISK_BLOCKED"):
        service._assert_live_submission_allowed("ctp", order)
