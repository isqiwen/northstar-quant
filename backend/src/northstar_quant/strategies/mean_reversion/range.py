"""A separate mean-reversion mechanism using range position, with no engine branch."""

from decimal import Decimal

from northstar_quant.factors.definition import Parameter, Result
from northstar_quant.strategies.definition import Decision, DecisionKind


class RangeReversion:
    strategy_id = "mean_reversion.range"
    revision = "1"
    parameters = (
        Parameter("edge", "区间两端宽度", "0.2", "0", "0.49", "range fraction"),
        Parameter(
            "target_fraction", "持仓限额比例", "0.5", "0.000000000000000001", "1", "limit fraction"
        ),
        Parameter("order_lifetime_seconds", "目标有效期", 3600, 1, 86400, "seconds"),
    )
    factor_slots = (("position", "range.position"),)

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
        position = factors["position"].value
        assert position is not None
        edge, target = Decimal(values["edge"]), Decimal(values["target_fraction"])
        exposure = target if position < edge else -target if position > 1 - edge else Decimal(0)
        return Decision(DecisionKind.SET_TARGET, exposure, "RANGE_EXTREME_REVERSION")


STRATEGY = RangeReversion()
