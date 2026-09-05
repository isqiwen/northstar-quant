"""Versioned, allowlisted provider adapters for local ingestion commands."""

from northstar_quant.data.ingestion.providers.shfe import ShfeDailyJsonAdapter

__all__ = ["ShfeDailyJsonAdapter"]
