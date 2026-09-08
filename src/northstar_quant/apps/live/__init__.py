"""Northstar Live · 实盘交易系统 management Web; kernel is independently supervised."""

from .application import application, create_app

__all__ = ["application", "create_app"]
