"""Explicit test-only authority for isolated CTP-sim mechanics.

Production code must obtain CTP-sim submission authority through
``CtpSimCandidateExecutor``.  Lower-layer tests use this helper only to exercise
simulator state-machine and recovery behavior without pretending that a
candidate provenance receipt existed.
"""

from __future__ import annotations

from northstar_quant.trading_execution.execution.models import (
    BrokerStateSnapshot,
    MarketQuoteSnapshot,
    OrderRequest,
)
from northstar_quant.trading_execution.orders.ctp_sim_submission_guard import (
    CtpSimSubmissionAuthority,
    CtpSimSubmissionGuard,
    _issue_ctp_sim_submission_authority,
)


class _TestOnlyCtpSimSubmissionGuard:
    """No-op guard confined to test fixtures, never exported by production code."""

    def reserve(self, order: OrderRequest) -> None:
        del order

    def assert_reserved(
        self,
        order: OrderRequest,
        *,
        snapshot: BrokerStateSnapshot,
        quotes: tuple[MarketQuoteSnapshot, ...],
    ) -> None:
        del order, snapshot, quotes

    def mark_submitted(
        self,
        order: OrderRequest,
        *,
        snapshot: BrokerStateSnapshot,
    ) -> None:
        del order, snapshot


def create_test_ctp_sim_submission_authority(
    guard: CtpSimSubmissionGuard | None = None,
) -> CtpSimSubmissionAuthority:
    """Issue an explicit test-only authority for lower-level simulator tests."""

    return _issue_ctp_sim_submission_authority(
        _TestOnlyCtpSimSubmissionGuard() if guard is None else guard,
        composition_owner_token=object(),
    )


__all__ = ["create_test_ctp_sim_submission_authority"]
