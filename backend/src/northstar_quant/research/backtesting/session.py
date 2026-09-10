"""One reproducible research loop shared by batch and incremental callers."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from copy import copy
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from uuid import UUID

from northstar_quant.accounting.amounts import decimal_text
from northstar_quant.accounting.fifo import Account, AppliedFill, FillFact
from northstar_quant.accounting.portfolio import PortfolioState
from northstar_quant.accounting.positions import Position
from northstar_quant.accounting.settlement import AppliedSettlement, SettlementFact
from northstar_quant.data_management.research import DatasetDetails
from northstar_quant.execution.orders import OrderStatus, OrderUpdate, PendingOrder, order_slice
from northstar_quant.market_data import Market, MarketBar
from northstar_quant.messaging import Endpoint, Topic
from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.risk import evaluate_risk
from northstar_quant.simulation import simulate_fill
from northstar_quant.strategies.runtime import StrategyRuntime
from northstar_quant.trading.environment import Environment
from northstar_quant.trading.kernel import FailurePolicy, TradingKernel


@dataclass(frozen=True, slots=True, init=False)
class TradingStep:
    """Only the new facts from one advance; callers own the full persistent log."""

    _point: str
    _decision: str | None
    fill: AppliedFill | None
    new_order: PendingOrder | None
    settlements: tuple[AppliedSettlement, ...]
    orders: tuple[OrderUpdate, ...]

    def __init__(
        self,
        point: dict[str, object],
        decision: dict[str, object] | None,
        fill: AppliedFill | None,
        new_order: PendingOrder | None,
        settlements: tuple[AppliedSettlement, ...] = (),
        orders: tuple[OrderUpdate, ...] = (),
    ) -> None:
        # Like ResearchResult, canonical bytes protect nested factor/metric values.
        object.__setattr__(self, "_point", json.dumps(point, sort_keys=True, allow_nan=False))
        object.__setattr__(
            self,
            "_decision",
            None if decision is None else json.dumps(decision, sort_keys=True, allow_nan=False),
        )
        object.__setattr__(self, "fill", fill)
        object.__setattr__(self, "new_order", new_order)
        object.__setattr__(self, "settlements", settlements)
        object.__setattr__(self, "orders", orders)

    @property
    def point(self) -> dict[str, object]:
        result: dict[str, object] = json.loads(self._point)
        return result

    @property
    def decision(self) -> dict[str, object] | None:
        result: dict[str, object] | None = (
            None if self._decision is None else json.loads(self._decision)
        )
        return result

    def to_dict(self) -> dict[str, object]:
        return {
            "settlements": [item.to_dict() for item in self.settlements],
            "orders": [item.to_dict() for item in self.orders],
            "point": dict(self.point),
            "decision": None if self.decision is None else dict(self.decision),
            "fill": None if self.fill is None else self.fill.to_dict(),
            "new_order": None if self.new_order is None else self.new_order.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> TradingStep:
        try:
            if set(value) != {"point", "decision", "fill", "new_order", "settlements", "orders"}:
                raise ValueError("trading step must contain all of its fact fields")
            raw_fill = value["fill"]
            fill = None
            if raw_fill is not None:
                item = _object(raw_fill)
                position = item["position_lots"]
                if type(position) is not int:
                    raise ValueError("ledger position must be integer lots")
                fill = AppliedFill(
                    FillFact.from_dict(item),
                    _money(item["realized_pnl"]),
                    position,
                    _money(item["cash"]),
                    _money(item["total_fees"]),
                    Position.from_dict(_object(item["gross_position"])),
                )
            raw_orders = value["orders"]
            if not isinstance(raw_orders, list):
                raise ValueError("order updates must be an ordered list")
            orders = tuple(OrderUpdate.from_dict(_object(item)) for item in raw_orders)
            raw_settlements = value["settlements"]
            if not isinstance(raw_settlements, list):
                raise ValueError("settlements must be an ordered list of facts")
            settlements = tuple(
                AppliedSettlement(
                    SettlementFact.from_dict(_object(item)),
                    _money(_object(item)["variation_pnl"]),
                    _money(_object(item)["cash"]),
                )
                for item in raw_settlements
            )
            return cls(
                dict(_object(value["point"])),
                None if value["decision"] is None else dict(_object(value["decision"])),
                fill,
                None
                if value["new_order"] is None
                else PendingOrder.from_dict(_object(value["new_order"])),
                settlements,
                orders,
            )
        except (KeyError, TypeError, ArithmeticError) as error:
            raise ValueError("invalid persisted trading step") from error


ADVANCE_BAR: Endpoint[MarketBar, TradingStep | None] = Endpoint("research.advance", MarketBar)
STEP_COMPLETED: Topic[TradingStep] = Topic("research.step.completed", TradingStep)


class TradingSession:
    """Bounded shared research/Paper core: simulate, account, strategy, then Risk.

    There is at most one pending target. Compatible decisions retain its fixed
    authorization; changes explicitly cancel its remainder before a new request.
    Recent identical observations are no-ops; older retries are owned by the
    caller's persistent input identity. Changed or time-regressing facts fail
    before mutation. History is bounded by lookback, not the number of steps.
    This remains a simulated execution loop, not a broker execution adapter.
    """

    # Bump for changed Strategy/Risk/Simulation/Accounting rules or checkpoint format.
    REVISION = "8"

    def __init__(
        self,
        market: Market,
        config: ResearchConfig,
        *,
        snapshot_id: UUID,
        content_hash: str,
        data_details: DatasetDetails | None = None,
    ) -> None:
        if not isinstance(snapshot_id, UUID) or re.fullmatch(r"[0-9a-f]{64}", content_hash) is None:
            raise ValueError("research requires an exact snapshot UUID and SHA-256 identity")
        if (
            not isinstance(market.contract_id, UUID)
            or not isinstance(market.price_tick, Decimal)
            or not isinstance(market.multiplier, Decimal)
            or not market.price_tick.is_finite()
            or not market.multiplier.is_finite()
            or market.price_tick <= 0
            or market.multiplier <= 0
            or type(market.interval_seconds) is not int
            or market.interval_seconds <= 0
        ):
            raise ValueError("market must have exact positive economics and interval")
        for value in (market.price_tick, market.multiplier):
            exponent = value.as_tuple().exponent
            if (
                not isinstance(exponent, int)
                or exponent < -18
                or len(value.as_tuple().digits) > 34
                or value.adjusted() > 33
            ):
                raise ValueError("market economics exceed the 34-digit/18-place financial domain")
        self.market, self.config = market, config
        self.snapshot_id, self.content_hash = snapshot_id, content_hash
        if data_details is not None and (
            data_details.summary.snapshot_id != snapshot_id
            or data_details.summary.content_hash != content_hash
        ):
            raise ValueError("source evidence does not belong to this research snapshot")
        if data_details is not None and data_details.volume_unit != "LOT":
            raise ValueError("simulation requires explicitly declared per-bar volume in lots")
        self._data_details = data_details
        self._settlements = () if data_details is None else data_details.settlements
        if any(fact.contract_id != market.contract_id for fact in self._settlements) or len(
            {fact.trading_day for fact in self._settlements}
        ) != len(self._settlements):
            raise ValueError(
                "research settlement facts must belong uniquely to the fixed contract/day"
            )
        self.account = Account(config.simulation.initial_cash, market)
        self.pending: PendingOrder | None = None
        self._policy = config.risk_policy()
        self._trader = StrategyRuntime(
            config.strategy, market.contract_id, market.interval_seconds, source_scope=content_hash
        )
        self._trading_day: date | None = None
        self._last: MarketBar | None = None
        self._bar_count = 0
        self._decision_count = 0
        self._last_decision: tuple[UUID, datetime] | None = None
        self._peak = config.simulation.initial_cash
        self._maximum_drawdown = Decimal(0)
        self._maximum_drawdown_fraction = Decimal(0)
        self.kernel = TradingKernel(
            ADVANCE_BAR,
            self._process,
            environment=Environment.BACKTEST,
            failure_policy=FailurePolicy.ROLLBACK,
            completed=STEP_COMPLETED,
        )
        self.kernel.start()

    def advance(self, bar: MarketBar) -> TradingStep | None:
        return self.kernel.advance(bar)

    def validate_inputs(self, bars: Sequence[MarketBar]) -> None:
        """Reject missing cross-day evidence before creating a durable run."""
        for before, after in zip(bars, bars[1:]):
            if before.trading_day != after.trading_day:
                self._settlement_between(before, after)

    def _settlement_between(self, before: MarketBar, after: MarketBar) -> SettlementFact:
        matches = tuple(
            fact for fact in self._settlements if fact.trading_day == before.trading_day
        )
        if (
            len(matches) != 1
            or matches[0].next_trading_day != after.trading_day
            or not before.completed_at
            <= matches[0].settled_at
            <= matches[0].available_at
            <= after.event_time
        ):
            raise ValueError("trading-day transition requires a fixed, causal settlement fact")
        return matches[0]

    def close(self) -> None:
        self.kernel.close()

    def _process(self, bar: MarketBar) -> TradingStep | None:
        self._validate_bar(bar)
        if not self._trader.accepts(bar):
            return None

        with localcontext() as context:
            context.prec = 96
            context.rounding = ROUND_HALF_EVEN
            # Scalar state and bounded strategy history are staged. The Account
            # rolls back only this event's projection and newly accepted fill.
            candidate = copy(self)
            candidate._trader = copy(self._trader)
            with self.account.transaction():
                result = candidate._advance(bar)
            self.__dict__.update(candidate.__dict__)
        # This is an in-memory completed research step, not a durable DB receipt.
        # Paper commits it with its checkpoint in its owning transaction.
        return result

    def _advance(self, bar: MarketBar) -> TradingStep:
        settlements: tuple[AppliedSettlement, ...] = ()
        orders: list[OrderUpdate] = []
        if self._trading_day is not None and self._trading_day != bar.trading_day:
            assert self._last is not None
            settlement = self._settlement_between(self._last, bar)
            if self.pending is not None:
                if self.pending.expires_at > settlement.settled_at:
                    raise ValueError("unexpired simulated order cannot cross settlement")
                orders.append(
                    OrderUpdate(
                        self.pending,
                        OrderStatus.EXPIRED,
                        settlement.settled_at,
                        "EXPIRED_BEFORE_SETTLEMENT",
                    )
                )
                self.pending = None
            settlements = (self.account.settle(settlement, at=bar.event_time),)
        self._trading_day = bar.trading_day
        fill = None
        if self.pending is not None:
            attempt = simulate_fill(
                self.pending,
                bar,
                self.market,
                fee_per_lot=self.config.simulation.fee_per_lot,
                slippage_ticks=self.config.simulation.slippage_ticks,
                max_volume_participation=self.config.simulation.max_volume_participation,
            )
            if attempt.fill is not None:
                fill = self.account.apply(attempt.fill)
                self.pending = replace(
                    self.pending, filled_lots=self.pending.filled_lots + attempt.fill.quantity_lots
                )
                status = (
                    OrderStatus.FILLED
                    if self.pending.remaining_lots == 0
                    else OrderStatus.PARTIALLY_FILLED
                )
                orders.append(OrderUpdate(self.pending, status, bar.available_at, attempt.reason))
                if self.pending.remaining_lots == 0:
                    self.pending = None
            elif attempt.reason == "EXPIRED":
                orders.append(
                    OrderUpdate(self.pending, OrderStatus.EXPIRED, bar.available_at, attempt.reason)
                )
                self.pending = None
            else:
                status = (
                    OrderStatus.PARTIALLY_FILLED
                    if self.pending.filled_lots
                    else OrderStatus.SUBMITTED
                )
                orders.append(OrderUpdate(self.pending, status, bar.available_at, attempt.reason))
        self._bar_count += 1
        self._last = bar
        equity = self.account.equity(bar.close)
        self._peak = max(self._peak, equity)
        drawdown = self._peak - equity
        drawdown_fraction = drawdown / self._peak
        self._maximum_drawdown = max(self._maximum_drawdown, drawdown)
        self._maximum_drawdown_fraction = max(self._maximum_drawdown_fraction, drawdown_fraction)
        point: dict[str, object] = {
            "observation_id": str(bar.observation_id),
            "at": bar.available_at.isoformat(),
            "close": decimal_text(bar.close),
            "cash": decimal_text(self.account.cash),
            "position_lots": self.account.position_lots,
            "realized_pnl": decimal_text(self.account.realized_pnl),
            "unrealized_pnl": decimal_text(self.account.unrealized_pnl(bar.close)),
            "total_fees": decimal_text(self.account.total_fees),
            "equity": decimal_text(equity),
            "drawdown": decimal_text(drawdown),
            "drawdown_fraction": decimal_text(drawdown_fraction),
        }
        decision: dict[str, object] | None = None
        signal = self._trader.advance(bar)
        assert signal is not None
        point["strategy"] = {
            "kind": signal.decision.kind.value,
            "reason": signal.decision.reason,
            "factors": {alias: result.to_dict() for alias, result in signal.factors},
        }
        intent = signal.intent
        if intent is not None:
            self._last_decision = (intent.observation_id, intent.generated_at)
            risk = evaluate_risk(
                intent,
                PortfolioState(bar.available_at, equity, self.account.position_lots, bar.close),
                self._policy,
                self.market,
            )
            decision = {
                "observation_id": str(bar.observation_id),
                "at": bar.available_at.isoformat(),
                "strategy_id": intent.strategy_id,
                "decision_kind": signal.decision.kind.value,
                "factors": {alias: result.to_dict() for alias, result in signal.factors},
                "target_fraction": decimal_text(intent.target_fraction),
                "outcome": risk.outcome.value,
                "reason": risk.reason,
                "desired_position_lots": risk.desired_position_lots,
                "approved_position_lots": risk.approved_position_lots,
                "side": None if risk.side is None else risk.side.value,
                "quantity_lots": risk.quantity_lots,
                "minimum_fill_price": None
                if risk.minimum_fill_price is None
                else decimal_text(risk.minimum_fill_price),
                "maximum_fill_price": None
                if risk.maximum_fill_price is None
                else decimal_text(risk.maximum_fill_price),
                "expires_at": risk.expires_at.isoformat(),
            }
            self._decision_count += 1
            plan = None
            if risk.quantity_lots:
                assert risk.side is not None
                assert risk.minimum_fill_price is not None and risk.maximum_fill_price is not None
                plan = order_slice(self.account.position, risk.side, risk.quantity_lots)
            if self.pending is not None:
                keep = (
                    plan is not None
                    and risk.minimum_fill_price is not None
                    and risk.maximum_fill_price is not None
                    and self.pending.side is risk.side
                    and self.pending.offset is plan[0]
                    and self.pending.remaining_lots <= plan[1]
                    and risk.minimum_fill_price <= self.pending.minimum_fill_price
                    and self.pending.maximum_fill_price <= risk.maximum_fill_price
                )
                if keep:
                    decision["retained_order_id"] = self.pending.order_id
                    status = (
                        OrderStatus.PARTIALLY_FILLED
                        if self.pending.filled_lots
                        else OrderStatus.SUBMITTED
                    )
                    orders.append(
                        OrderUpdate(
                            self.pending, status, bar.available_at, "AUTHORIZATION_RETAINED"
                        )
                    )
                    return TradingStep(
                        point, decision, fill, self.pending, settlements, tuple(orders)
                    )
                orders.append(
                    OrderUpdate(
                        self.pending, OrderStatus.CANCELED, bar.available_at, "TARGET_REPLACED"
                    )
                )
                self.pending = None
            if plan is not None:
                assert risk.side is not None
                assert risk.minimum_fill_price is not None and risk.maximum_fill_price is not None
                offset, quantity = plan
                decision["order_quantity_lots"] = quantity
                decision["order_offset"] = offset.value
                self.pending = PendingOrder(
                    intent.intent_id,
                    bar.observation_id,
                    bar.available_at,
                    risk.expires_at,
                    risk.side,
                    offset,
                    quantity,
                    risk.minimum_fill_price,
                    risk.maximum_fill_price,
                )
                orders.append(
                    OrderUpdate(
                        self.pending, OrderStatus.SUBMITTED, bar.available_at, "RISK_AUTHORIZED"
                    )
                )
        return TradingStep(point, decision, fill, self.pending, settlements, tuple(orders))

    def summary(self) -> dict[str, object]:
        """Current metrics, including a genuinely empty initialized account."""

        with localcontext() as context:
            context.prec = 96
            context.rounding = ROUND_HALF_EVEN
            unrealized = (
                Decimal(0) if self._last is None else self.account.unrealized_pnl(self._last.close)
            )
            equity = self.account.cash + unrealized
            return {
                "bar_count": self._bar_count,
                "decision_count": self._decision_count,
                "fill_count": self.account.fill_count,
                "initial_cash": decimal_text(self.config.simulation.initial_cash),
                "ending_cash": decimal_text(self.account.cash),
                "ending_position_lots": self.account.position_lots,
                "realized_pnl": decimal_text(self.account.realized_pnl),
                "unrealized_pnl": decimal_text(unrealized),
                "total_fees": decimal_text(self.account.total_fees),
                "ending_equity": decimal_text(equity),
                "total_return": decimal_text(equity / self.config.simulation.initial_cash - 1),
                "max_drawdown": decimal_text(self._maximum_drawdown),
                "max_drawdown_fraction": decimal_text(self._maximum_drawdown_fraction),
            }

    def checkpoint(self) -> dict[str, object]:
        """Persist only the current projection and bounded strategy warmup.

        This is acceleration material. Restoration requires an independently
        rebuilt Account; the caller protects this document and its committed
        step identity in the same transaction as the ledger facts.
        """

        return {
            "engine_revision": self.REVISION,
            "environment": self.kernel.status.environment.value,
            "strategy_state": dict(self._trader.state),
            "last_decision": None
            if self._last_decision is None
            else {
                "observation_id": str(self._last_decision[0]),
                "at": self._last_decision[1].isoformat(),
            },
            "snapshot_id": str(self.snapshot_id),
            "content_hash": self.content_hash,
            "market": {
                name: str(value)
                if isinstance(value, UUID)
                else decimal_text(value)
                if isinstance(value, Decimal)
                else value
                for name, value in asdict(self.market).items()
            },
            "config": self.config.to_dict(),
            "account": self.account.checkpoint(),
            "history": [_bar_dict(bar) for bar in self._trader.history],
            "last": None if self._last is None else _bar_dict(self._last),
            "trading_day": None if self._trading_day is None else self._trading_day.isoformat(),
            "pending": None if self.pending is None else self.pending.to_dict(),
            "bar_count": self._bar_count,
            "decision_count": self._decision_count,
            "peak": decimal_text(self._peak),
            "maximum_drawdown": decimal_text(self._maximum_drawdown),
            "maximum_drawdown_fraction": decimal_text(self._maximum_drawdown_fraction),
        }

    @classmethod
    def from_checkpoint(
        cls,
        market: Market,
        config: ResearchConfig,
        *,
        snapshot_id: UUID,
        content_hash: str,
        checkpoint: dict[str, object],
        account: Account,
        data_details: DatasetDetails | None = None,
    ) -> TradingSession:
        """Resume bounded computation after comparing the independently rebuilt ledger.

        No market history is replayed here. Account facts are never read from
        this checkpoint to construct balances; the persistent caller supplies
        its ledger-derived Account and verifies committed step/checkpoint hashes.
        """

        session = cls(
            market,
            config,
            snapshot_id=snapshot_id,
            content_hash=content_hash,
            data_details=data_details,
        )
        initial = session.checkpoint()
        if not isinstance(checkpoint, dict) or set(checkpoint) != set(initial):
            raise ValueError("checkpoint fields do not match the current trading implementation")
        for name in ("engine_revision", "snapshot_id", "content_hash", "market", "config"):
            if checkpoint[name] != initial[name]:
                raise ValueError("checkpoint differs from its fixed input or configuration")
        if (
            account.market != market
            or account.initial_cash != config.simulation.initial_cash
            or checkpoint["account"] != account.checkpoint()
        ):
            raise ValueError("checkpoint account differs from the verified fill ledger")
        count, decisions = checkpoint["bar_count"], checkpoint["decision_count"]
        if (
            type(count) is not int
            or not 0 <= count <= 100000
            or type(decisions) is not int
            or not 0 <= decisions <= count
            or account.fill_count > max(0, count - 1)
        ):
            raise ValueError("checkpoint counters differ from the trading sequence")
        history = checkpoint["history"]
        if not isinstance(history, list) or len(history) != min(
            count, (config.strategy.history_bars - 1) + 1
        ):
            raise ValueError("checkpoint warmup must have the exact bounded history")
        previous = None
        accepted_history = []
        history_ids: set[UUID] = set()
        for item in history:
            bar = _bar_from_dict(_object(item))
            session._validate_bar(bar)
            if bar.observation_id in history_ids:
                raise ValueError("checkpoint history repeats an observation identity")
            history_ids.add(bar.observation_id)
            if previous is not None and (
                bar.event_time <= previous.event_time
                or bar.available_at < previous.available_at
                or bar.trading_day < previous.trading_day
                or bar.observation_id == previous.observation_id
            ):
                raise ValueError("checkpoint history is not causally ordered")
            accepted_history.append(bar)
            previous = bar
        for before, after in zip(accepted_history, accepted_history[1:]):
            if before.trading_day != after.trading_day and not any(
                fact.trading_day == before.trading_day
                and fact.next_trading_day == after.trading_day
                and before.completed_at <= fact.settled_at <= fact.available_at <= after.event_time
                for fact in session._settlements
            ):
                raise ValueError("checkpoint day transition lacks fixed settlement evidence")
        session._last = previous
        session._trading_day = None if previous is None else previous.trading_day
        if checkpoint["last"] != (None if previous is None else _bar_dict(previous)) or checkpoint[
            "trading_day"
        ] != (None if previous is None else previous.trading_day.isoformat()):
            raise ValueError("checkpoint last observation differs from its warmup history")
        last_decision = checkpoint["last_decision"]
        if last_decision is not None:
            decision = _object(last_decision)
            session._last_decision = (
                UUID(str(decision["observation_id"])),
                datetime.fromisoformat(str(decision["at"])),
            )
            if (
                previous is None
                or session._last_decision[1].utcoffset() != timedelta(0)
                or session._last_decision[1] > previous.available_at
            ):
                raise ValueError("checkpoint decision is later than its observed inputs")
        if (decisions == 0) != (last_decision is None):
            raise ValueError("checkpoint counters differ from its last decision")
        pending = checkpoint["pending"]
        session.pending = None if pending is None else PendingOrder.from_dict(_object(pending))
        if session.pending is not None and (
            previous is None
            or not decisions
            or session._last_decision is None
            or session.pending.submitted_at > session._last_decision[1]
            or session.pending.remaining_lots == 0
            or session.pending.quantity_lots > config.risk.max_lots
            or session.pending.offset
            is not order_slice(
                account.position, session.pending.side, session.pending.remaining_lots
            )[0]
        ):
            raise ValueError("checkpoint pending order differs from its last decision")
        session.account = account
        session._bar_count, session._decision_count = count, decisions
        raw_state = _object(checkpoint["strategy_state"])
        if any(type(value) not in {str, int} for value in raw_state.values()):
            raise ValueError("invalid strategy checkpoint state")
        session._trader = StrategyRuntime(
            config.strategy,
            market.contract_id,
            market.interval_seconds,
            source_scope=content_hash,
            history=tuple(accepted_history),
            state=tuple((key, value) for key, value in raw_state.items()),  # type: ignore[misc]
        )
        session._peak = _money(checkpoint["peak"])
        session._maximum_drawdown = _money(checkpoint["maximum_drawdown"])
        session._maximum_drawdown_fraction = _money(checkpoint["maximum_drawdown_fraction"])
        if (
            session._peak < config.simulation.initial_cash
            or session._maximum_drawdown < 0
            or session._maximum_drawdown_fraction < 0
        ):
            raise ValueError("checkpoint drawdown state is invalid")
        if previous is not None:
            with localcontext() as context:
                context.prec = 96
                context.rounding = ROUND_HALF_EVEN
                equity = account.equity(previous.close)
                current_drawdown = session._peak - equity
                if (
                    current_drawdown < 0
                    or session._maximum_drawdown < current_drawdown
                    or session._maximum_drawdown_fraction < current_drawdown / session._peak
                ):
                    raise ValueError("checkpoint drawdown disagrees with current account equity")
        if not count and session.checkpoint() != initial:
            raise ValueError("empty checkpoint must describe its initialized account")
        if session.checkpoint() != checkpoint:
            raise ValueError("checkpoint representation is not canonical")
        return session

    def _validate_bar(self, bar: MarketBar) -> None:
        if not isinstance(bar, MarketBar):
            raise ValueError("research requires canonical observations")
        bar.validate(
            interval_seconds=self.market.interval_seconds, price_tick=self.market.price_tick
        )


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("persisted trading state must be an object")
    return value


def _money(value: object) -> Decimal:
    if not isinstance(value, str) or len(value) > 256:
        raise ValueError("persisted trading amounts must be exact decimal strings")
    try:
        result = Decimal(value)
    except ArithmeticError as error:
        raise ValueError("invalid persisted trading amount") from error
    exponent = result.as_tuple().exponent
    if (
        not result.is_finite()
        or not isinstance(exponent, int)
        or exponent < -192
        or result.adjusted() > 96
        or len(result.as_tuple().digits) > 192
    ):
        raise ValueError("persisted trading amount must be finite and bounded")
    return result


def _bar_dict(bar: MarketBar) -> dict[str, object]:
    return {
        "observation_id": str(bar.observation_id),
        "event_time": bar.event_time.isoformat(),
        "completed_at": bar.completed_at.isoformat(),
        "available_at": bar.available_at.isoformat(),
        "trading_day": bar.trading_day.isoformat(),
        "close": decimal_text(bar.close),
        "volume": decimal_text(bar.volume),
    }


def _bar_from_dict(value: dict[str, object]) -> MarketBar:
    try:
        return MarketBar(
            UUID(str(value["observation_id"])),
            datetime.fromisoformat(str(value["event_time"])),
            datetime.fromisoformat(str(value["completed_at"])),
            datetime.fromisoformat(str(value["available_at"])),
            date.fromisoformat(str(value["trading_day"])),
            _money(value["close"]),
            _money(value["volume"]),
        )
    except (KeyError, TypeError, ArithmeticError) as error:
        raise ValueError("invalid persisted warmup observation") from error
