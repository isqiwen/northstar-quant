from datetime import UTC, datetime, timedelta

import pytest

from northstar_quant.portfolio_risk.risk import RiskState, RiskStateError, RiskStateSnapshot


def test_halt_requires_named_manual_recovery_before_normal():
    now = datetime(2026, 8, 21, 9, tzinfo=UTC)
    halted = RiskStateSnapshot.initial(occurred_at=now).transition(target=RiskState.HALT, occurred_at=now + timedelta(minutes=1), reason="limit breach")
    with pytest.raises(RiskStateError, match="forbidden"):
        halted.transition(target=RiskState.NORMAL, occurred_at=now + timedelta(minutes=2), reason="automatic")
    recovery = halted.transition(target=RiskState.MANUAL_RECOVERY, occurred_at=now + timedelta(minutes=2), reason="review", recovery_approver_id="risk-owner")
    assert recovery.transition(target=RiskState.NORMAL, occurred_at=now + timedelta(minutes=3), reason="recovered").predecessor_hash == recovery.state_hash


def test_manual_recovery_requires_approver():
    now = datetime(2026, 8, 21, 9, tzinfo=UTC)
    with pytest.raises(RiskStateError, match="named approver"):
        RiskStateSnapshot(RiskState.MANUAL_RECOVERY, now, "review")
