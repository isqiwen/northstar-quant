"""Fixed pre-trade risk policies and effective futures terms, without account mutation."""

from dataclasses import replace
from decimal import Decimal
from types import MappingProxyType

from northstar_quant.accounting.portfolio import PortfolioState
from northstar_quant.accounting.terms import FuturesTerms, ordered_terms
from northstar_quant.execution.orders import Offset, Side
from northstar_quant.market_data import Market
from northstar_quant.strategies import StrategyIntent

from .sizing import Outcome, RiskDecision, RiskPolicy, evaluate_risk
from .terms import order_budget, policy_for_terms


class RiskEngine:
    """Bind policies once; calculate from a supplied current read-only account view.

    A result is a risk allowance, never a broker send authorization. Execution
    owns durable reservations and Accounting owns confirmed account changes.
    """

    def __init__(
        self, market: Market, policy: RiskPolicy, terms: tuple[FuturesTerms, ...] = ()
    ) -> None:
        revisions = ordered_terms(terms)
        if any(item.contract_id != market.contract_id for item in revisions):
            raise ValueError("risk terms belong to another fixed contract")
        self._market, self._policy = market, policy
        self._terms = MappingProxyType({item.terms_id: item for item in revisions})
        self._policies = MappingProxyType(
            {item.terms_id: policy_for_terms(policy, item, market) for item in revisions}
        )

    def _bound(self, terms: FuturesTerms | None) -> None:
        if terms is None:
            if self._terms:
                raise ValueError("fixed risk terms cannot be bypassed")
        elif self._terms.get(terms.terms_id) != terms:
            raise ValueError("risk terms differ from the fixed revision")

    def evaluate(
        self, intent: StrategyIntent, state: PortfolioState, *, terms: FuturesTerms | None = None
    ) -> RiskDecision:
        self._bound(terms)
        if terms is not None:
            terms.require_available(state.observed_at)
        risk = evaluate_risk(
            intent,
            state,
            self._policy if terms is None else self._policies[terms.terms_id],
            self._market,
        )
        if terms is None:
            return risk
        minimum = (
            None
            if risk.minimum_fill_price is None
            else max(risk.minimum_fill_price, terms.lower_limit)
        )
        maximum = (
            None
            if risk.maximum_fill_price is None
            else min(risk.maximum_fill_price, terms.upper_limit)
        )
        if minimum is not None and maximum is not None and minimum > maximum:
            risk = replace(
                risk,
                outcome=Outcome.REJECT,
                reason="NO_PRICE_WITHIN_EFFECTIVE_TERMS",
                approved_position_lots=None,
                side=None,
                quantity_lots=0,
            )
            minimum, maximum = None, None
        return replace(
            risk,
            minimum_fill_price=minimum,
            maximum_fill_price=maximum,
            expires_at=min(risk.expires_at, terms.effective_until),
        )

    def budget(
        self,
        *,
        side: Side,
        offset: Offset,
        maximum_fill_price: Decimal,
        terms: FuturesTerms | None = None,
    ) -> tuple[Decimal, Decimal]:
        self._bound(terms)
        return order_budget(
            self._policy,
            self._market,
            side=side,
            offset=offset,
            maximum_fill_price=maximum_fill_price,
            terms=terms,
        )
