"""Test-only construction seam for the closed CTP-sim candidate executor."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from northstar_quant.application.ctp_sim_candidate_execution import (
    ContractAuthorityResolver,
    CtpSimCandidateExecutor,
)
from northstar_quant.foundation.config.settings import Settings
from northstar_quant.trading_execution.broker.simulated_state import SessionFactory


def create_test_ctp_sim_candidate_executor(
    *,
    settings_provider: Callable[[], Settings] | None = None,
    clock: Callable[[], datetime] | None = None,
    contract_authority_resolver: ContractAuthorityResolver | None = None,
    session_factory: SessionFactory | None = None,
) -> CtpSimCandidateExecutor:
    """Inject deterministic runtime facts only from test composition code.

    The production constructor has no ambient dependency injection seam: it
    always binds uncached settings and the production clock.  Tests need a
    deterministic simulator, so this helper changes private slots only after
    the production object has been constructed.
    """

    executor = CtpSimCandidateExecutor()
    if settings_provider is not None:
        object.__setattr__(executor, "_settings_provider", settings_provider)
    if clock is not None:
        object.__setattr__(executor, "_clock", clock)
    if contract_authority_resolver is not None:
        object.__setattr__(
            executor,
            "_contract_authority_resolver",
            contract_authority_resolver,
        )
    if session_factory is not None:
        object.__setattr__(executor, "_broker_session_factory", session_factory)
    return executor


__all__ = ["create_test_ctp_sim_candidate_executor"]
