"""Commands and bounded failures for provider-mediated ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID


@dataclass(frozen=True)
class ShfeDailyFetchCommand:
    """One bounded official-SHFE daily response for one cataloged series."""

    series_id: UUID
    source_symbol: str
    trading_day: date
    available_at: datetime
    idempotency_key: str
    correlation_id: str
    source_admission_review_id: UUID | None = None
    causation_id: str | None = None
    recovery_of_retrieval_id: UUID | None = None


@dataclass(frozen=True)
class RecoverShfeDailyRetrievalCommand:
    """A human-approved terminal recovery of one stale SHFE retrieval.

    The command never fetches from SHFE.  A later explicit child retrieval may
    use ``recovery_of_retrieval_id`` only after this command records its audit
    evidence and makes the parent terminal.  ``reason`` is a controlled external
    incident reference, not a free-form note or a place for source material.
    """

    retrieval_id: UUID
    operator_id: str
    reason: str
    idempotency_key: str
    correlation_id: str
    causation_id: str | None = None


class ProviderFetchError(RuntimeError):
    """A non-secret failure while retrieving an allowlisted provider response."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        retryable: bool,
        http_status: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail[:1024]
        self.retryable = retryable
        self.http_status = http_status
