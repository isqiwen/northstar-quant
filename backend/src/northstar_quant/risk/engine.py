"""Fixed pre-trade risk policies and effective futures terms, without account mutation."""

from dataclasses import replace
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from types import MappingProxyType

from northstar_quant.accounting.portfolio import PortfolioState, PortfolioValuation
from northstar_quant.accounting.terms import FuturesTerms, ordered_terms
from northstar_quant.execution.orders import Offset, OrderBudget, PendingOrder, Side
from northstar_quant.market_data import Instrument
from northstar_quant.strategies import StrategyIntent

from .sizing import Outcome, RiskDecision, RiskPolicy, evaluate_risk
from .terms import order_budget, policy_for_terms


class RiskEngine:
    """Bind policies once; calculate from a supplied current read-only account view.

    A result is a risk allowance, never a broker send authorization. Execution
    owns durable reservations and Accounting owns confirmed account changes.
    """

    def __init__(
        self, market: Instrument, policy: RiskPolicy, terms: tuple[FuturesTerms, ...] = ()
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

    def evaluate_portfolio(
        self,
        intent: StrategyIntent,
        portfolio: PortfolioValuation,
        *,
        at: datetime,
        terms: FuturesTerms | None = None,
        pending: tuple[PendingOrder, ...] = (),
    ) -> RiskDecision:
        """Size against one marked account including other unresolved commitments.

        Pending orders are supplied by Execution, including expired authorizations
        whose terminal outcome is unknown. This does not release or reserve funds.
        The same contract must resolve its working order before a new allowance;
        historical replacement planning may evaluate its prospective post-cancel
        state, but must confirm cancellation before submitting the replacement.
        """
        if at != portfolio.observed_at:
            raise ValueError("portfolio observation time differs from the risk decision")
        holdings = {item.market.contract_id: item for item in portfolio.holdings}
        if len(holdings) != len(portfolio.holdings):
            raise ValueError("portfolio repeats a contract")
        own = holdings.get(self._market.contract_id)
        if own is None or own.market != self._market or own.mark is None:
            raise ValueError("risk requires its fixed contract and a current mark")
        if terms is not None and own.terms_id != terms.terms_id:
            raise ValueError("portfolio and risk must use the same effective terms")
        if own.position.long_today + own.position.long_yesterday and (
            own.position.short_today + own.position.short_yesterday
        ):
            raise ValueError("net-target sizing cannot hide simultaneous long and short inventory")
        if len({order.order_id for order in pending}) != len(pending):
            raise ValueError("pending order identity is duplicated")
        if any(order.contract_id == self._market.contract_id for order in pending):
            raise ValueError("resolve the contract's working order before sizing another order")
        with localcontext() as context:
            context.prec = 192
            context.rounding = ROUND_HALF_EVEN
            gross, margin, fees = Decimal(0), Decimal(0), Decimal(0)
            for identity, holding in holdings.items():
                if identity == self._market.contract_id:
                    continue
                gross += holding.gross_exposure
                if holding.gross_exposure and holding.margin_used is None:
                    raise ValueError("other holdings require known effective margin")
                margin += holding.margin_used or Decimal(0)
            for order in pending:
                order_holding = holdings.get(order.contract_id)
                if order_holding is None or order.submitted_at > at or not order.remaining_lots:
                    raise ValueError("pending order is outside the current fixed account view")
                fees += order.remaining_lots * (order.budget.fee + order.budget.loss)
                margin += order.remaining_lots * order.budget.margin
                gross += order.remaining_lots * order.budget.gross
            state = PortfolioState(
                at,
                portfolio.equity,
                own.position.net_lots,
                own.mark,
                gross,
                margin,
                fees,
            )
            return self.evaluate(intent, state, terms=terms)

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
    ) -> OrderBudget:
        self._bound(terms)
        return order_budget(
            self._policy,
            self._market,
            side=side,
            offset=offset,
            maximum_fill_price=maximum_fill_price,
            terms=terms,
        )
