"""Small thread-safe in-process metrics registry with deterministic text export."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from threading import RLock


class MetricsError(ValueError):
    """Metrics input is invalid or would make exported evidence ambiguous."""


@dataclass(frozen=True, slots=True)
class MetricSample:
    name: str
    labels: tuple[tuple[str, str], ...]
    value: float


class MetricsRegistry:
    """Explicit counter/gauge collection; no domain or network dependency."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._lock = RLock()

    def increment(self, name: str, value: float = 1.0, **labels: str) -> None:
        self._write(name, value, labels, additive=True)

    def gauge(self, name: str, value: float, **labels: str) -> None:
        self._write(name, value, labels, additive=False)

    def samples(self) -> tuple[MetricSample, ...]:
        with self._lock:
            return tuple(
                MetricSample(name, labels, value)
                for (name, labels), value in sorted(self._values.items())
            )

    def export_prometheus(self) -> str:
        return "".join(
            f"{sample.name}{_render_labels(sample.labels)} {sample.value:g}\n"
            for sample in self.samples()
        )

    def _write(self, name: str, value: float, labels: dict[str, str], *, additive: bool) -> None:
        if not isinstance(name, str) or not name.replace("_", "").isalnum() or not name:
            raise MetricsError("METRIC_NAME_INVALID: metric names use letters, digits and underscores.")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise MetricsError("METRIC_VALUE_INVALID: metric value must be finite.")
        normalized = tuple(sorted((_label(key, item) for key, item in labels.items())))
        key = (name, normalized)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + float(value) if additive else float(value)


def _label(key: object, value: object) -> tuple[str, str]:
    if not isinstance(key, str) or not key.replace("_", "").isalnum() or not key:
        raise MetricsError("METRIC_LABEL_INVALID: label names use letters, digits and underscores.")
    if not isinstance(value, str) or not value:
        raise MetricsError("METRIC_LABEL_VALUE_INVALID: label values must be non-empty strings.")
    return key, value


def _render_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{key}="{value.replace(chr(34), chr(92) + chr(34))}"' for key, value in labels) + "}"


__all__ = ["MetricSample", "MetricsError", "MetricsRegistry"]
