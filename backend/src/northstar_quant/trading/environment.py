"""Execution context fixed at node construction, never trading authorization."""

from enum import StrEnum


class Environment(StrEnum):
    BACKTEST = "BACKTEST"
    SANDBOX = "SANDBOX"
    LIVE = "LIVE"
