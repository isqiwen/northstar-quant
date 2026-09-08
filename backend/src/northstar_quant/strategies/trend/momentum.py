"""Thresholded return maps to a position-limit fraction; no factor recalculation."""

from decimal import Decimal

from northstar_quant.factors.definition import Parameter, Result
from northstar_quant.strategies.definition import Decision, DecisionKind


class Momentum:
    strategy_id = "trend.momentum"
    revision = "1"
    parameters = (
        Parameter("threshold", "动量阈值", "0.005", "0", "1", "return"),
        Parameter(
            "target_fraction", "持仓限额比例", "0.5", "0.000000000000000001", "1", "limit fraction"
        ),
        Parameter("order_lifetime_seconds", "目标有效期", 3600, 1, 86400, "seconds"),
    )
    factor_slots = (("momentum", "trend.return"),)

    def validate_state(self, state: tuple[tuple[str, str | int], ...]) -> None:
        if state:
            raise ValueError("this strategy is stateless")

    def decide(
        self,
        factors: dict[str, Result],
        values: dict[str, str | int],
        state: tuple[tuple[str, str | int], ...],
    ) -> Decision:
        self.validate_state(state)
        value = factors["momentum"].value
        assert value is not None
        threshold, target = Decimal(values["threshold"]), Decimal(values["target_fraction"])
        exposure = target if value > threshold else -target if value < -threshold else Decimal(0)
        return Decision(DecisionKind.SET_TARGET, exposure, "THRESHOLDED_RETURN")


STRATEGY = Momentum()
