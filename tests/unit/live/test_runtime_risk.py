"""盘中实时风控纯评估测试。"""

from datetime import UTC, datetime, timedelta

from northstar_quant.config.settings import get_settings
from northstar_quant.execution.models import (
    BrokerStateSnapshot,
    MarketQuoteSnapshot,
    PositionSnapshot,
)
from northstar_quant.live.runtime_risk import assess_runtime_risk


def _assessment(
    *,
    broker: str = "ctp",
    quote_age_seconds: int = 0,
    margin: float = 20_000.0,
    kill_switch: bool = False,
):
    checked_at = datetime(2026, 7, 31, 1, 5, tzinfo=UTC)
    settings = get_settings().model_copy(
        update={"kill_switch_enabled": kill_switch}
    )
    state = BrokerStateSnapshot(
        positions=[
            PositionSnapshot(
                symbol="RB2610",
                qty=1.0,
                market_price=3500.0,
                asof=checked_at,
            )
        ],
        account_values={
            "Balance": 100_000.0,
            "Available": 80_000.0,
            "CurrMargin": margin,
        },
        account="ctp-test",
        state_complete=True,
        asof=checked_at,
    )
    quotes = [
        MarketQuoteSnapshot(
            symbol="RB2610",
            bid=3499.0,
            ask=3501.0,
            last=3500.0,
            asof=checked_at - timedelta(seconds=quote_age_seconds),
        )
    ]
    return assess_runtime_risk(
        profile_id="cn_futures_daily_live",
        broker=broker,
        account="ctp-test",
        state=state,
        quotes=quotes if broker != "paper" else [],
        required_symbols=["RB2610"],
        settings=settings,
        checked_at=checked_at,
    )


def test_runtime_risk_accepts_fresh_account_margin_and_quotes():
    assessment = _assessment()

    assert assessment.can_submit is True
    assert assessment.blocking_checks == ()


def test_runtime_risk_blocks_stale_quotes_and_high_margin():
    assessment = _assessment(
        quote_age_seconds=60,
        margin=80_000.0,
    )

    assert assessment.can_submit is False
    codes = {check.code for check in assessment.blocking_checks}
    assert codes == {"margin_usage", "market_quotes"}


def test_runtime_risk_kill_switch_blocks_submission_immediately():
    assessment = _assessment(kill_switch=True)

    assert assessment.can_submit is False
    assert assessment.blocking_checks[0].code == "kill_switch"


def test_paper_runtime_risk_treats_missing_quotes_as_warning():
    assessment = _assessment(broker="paper")

    assert assessment.can_submit is True
    assert {check.code for check in assessment.warning_checks} == {
        "market_quotes"
    }
