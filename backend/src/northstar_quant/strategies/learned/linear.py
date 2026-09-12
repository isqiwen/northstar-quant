"""Fixed learned coefficients consume causal factors; fitting belongs to Research."""

from decimal import Decimal

from northstar_quant.factors.definition import Parameter, Result
from northstar_quant.strategies.definition import Decision, DecisionKind


class LinearReturn:
    strategy_id = "learned.linear_return"
    revision = "1"
    parameters = (
        Parameter("intercept", "固定截距", "0", "-1000000", "1000000", "return"),
        Parameter("fast_weight", "短窗口权重", "0", "-1000000", "1000000", "coefficient"),
        Parameter("slow_weight", "长窗口权重", "0", "-1000000", "1000000", "coefficient"),
        Parameter("threshold", "预测阈值", "0.001", "0", "1", "return"),
        Parameter(
            "target_fraction", "持仓限额比例", "0.5", "0.000000000000000001", "1", "limit fraction"
        ),
        Parameter("order_lifetime_seconds", "目标有效期", 3600, 1, 86400, "seconds"),
    )
    factor_slots = (("fast", "trend.return"), ("slow", "trend.return"))

    def validate_state(self, state: tuple[tuple[str, str | int], ...]) -> None:
        if state:
            raise ValueError("the fitted linear strategy is stateless")

    def decide(
        self,
        factors: dict[str, Result],
        values: dict[str, str | int],
        state: tuple[tuple[str, str | int], ...],
    ) -> Decision:
        self.validate_state(state)
        fast, slow = factors["fast"].value, factors["slow"].value
        assert fast is not None and slow is not None
        prediction = (
            Decimal(values["intercept"])
            + Decimal(values["fast_weight"]) * fast
            + Decimal(values["slow_weight"]) * slow
        )
        threshold, target = Decimal(values["threshold"]), Decimal(values["target_fraction"])
        exposure = (
            target if prediction > threshold else -target if prediction < -threshold else Decimal(0)
        )
        return Decision(DecisionKind.SET_TARGET, exposure, "FIXED_LINEAR_RETURN_PREDICTION")


STRATEGY = LinearReturn()
