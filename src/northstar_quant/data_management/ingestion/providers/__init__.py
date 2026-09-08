"""Versioned, allowlisted provider adapters for local ingestion commands."""

from northstar_quant.data_management.ingestion.providers.shfe import ShfeDailyJsonAdapter

__all__ = ["ShfeDailyJsonAdapter"]
