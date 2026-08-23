"""Final CTP-sim submission authority contract.

The trading-execution domain owns this narrow contract so its lower layers can
require final authorization without importing application composition code.
An arbitrary structural ``Protocol`` implementation is deliberately *not*
enough to unlock a simulator: the application composition boundary must obtain
an opaque authority from the private issuer below.  This keeps a normal source
caller from replacing the candidate gate with a no-op object while preserving a
small, test-only way to exercise isolated simulator mechanics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from northstar_quant.trading_execution.execution.models import (
        BrokerStateSnapshot,
        MarketQuoteSnapshot,
        OrderRequest,
    )


@runtime_checkable
class CtpSimSubmissionGuard(Protocol):
    """Two-phase candidate authorization for one exact CTP-sim order.

    ``reserve`` is called within the durable-intent transaction before the
    broker action.  ``assert_reserved`` is called by the simulator immediately
    before it mutates its own state, closing the adapter-level bypass and
    time-of-check/time-of-use gap.
    """

    def reserve(self, order: OrderRequest) -> None:
        """Append a one-time reservation for this canonical order or raise."""

    def assert_reserved(
        self,
        order: OrderRequest,
        *,
        snapshot: BrokerStateSnapshot,
        quotes: tuple[MarketQuoteSnapshot, ...],
    ) -> None:
        """Prove exact reservation and locked simulator state or raise."""

    def mark_submitted(
        self,
        order: OrderRequest,
        *,
        snapshot: BrokerStateSnapshot,
    ) -> None:
        """Advance from the simulator's already-locked post-submit snapshot."""


_AUTHORITY_ISSUER = object()


class CtpSimSubmissionAuthority:
    """Opaque capability bound to one final CTP-sim guard instance.

    ``CtpSimBrokerAdapter`` and ``DurableBrokerAdapter`` accept this capability,
    rather than accepting a structurally matching guard directly.  Its
    constructor is sealed with a module-private issuer; production code may
    only obtain one through the application composition boundary's narrow
    private import.  The project architecture test restricts that import to
    the candidate executor.  Tests may deliberately use the same private
    issuer only to cover lower-level simulator behavior, never as a supported
    runtime route.
    """

    __slots__ = ("__guard",)

    def __init__(
        self,
        guard: CtpSimSubmissionGuard,
        *,
        _issuer: object | None = None,
    ) -> None:
        if _issuer is not _AUTHORITY_ISSUER:
            raise PermissionError(
                "CTP_SIM_SUBMISSION_AUTHORITY_ISSUER_REQUIRED"
            )
        if not isinstance(guard, CtpSimSubmissionGuard):
            raise TypeError("CTP_SIM_SUBMISSION_GUARD_INVALID")
        self.__guard = guard

    def is_bound_to(self, guard: object) -> bool:
        """Return whether this capability is bound to the exact private gate."""

        return self.__guard is guard

    def _guard_for_composition(self) -> CtpSimSubmissionGuard:
        """Return the bound guard only to the application composition root.

        This deliberately private escape hatch is needed because the executor
        must configure its own newly issued gate after creating the isolated
        adapter.  It is not exported from ``orders`` and architecture tests
        restrict its production use to that composition module.
        """

        return self.__guard

    def reserve(self, order: OrderRequest) -> None:
        self.__guard.reserve(order)

    def assert_reserved(
        self,
        order: OrderRequest,
        *,
        snapshot: BrokerStateSnapshot,
        quotes: tuple[MarketQuoteSnapshot, ...],
    ) -> None:
        self.__guard.assert_reserved(order, snapshot=snapshot, quotes=quotes)

    def mark_submitted(
        self,
        order: OrderRequest,
        *,
        snapshot: BrokerStateSnapshot,
    ) -> None:
        self.__guard.mark_submitted(order, snapshot=snapshot)


def _issue_ctp_sim_submission_authority(
    guard: CtpSimSubmissionGuard,
) -> CtpSimSubmissionAuthority:
    """Issue the non-public authority used by the candidate composition root."""

    return CtpSimSubmissionAuthority(guard, _issuer=_AUTHORITY_ISSUER)


__all__ = ["CtpSimSubmissionGuard"]
