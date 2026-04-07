"""日志初始化模块。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

import structlog

from northstar_quant.config.settings import get_settings
from northstar_quant.config.yaml_loader import load_yaml

_STANDARD_LOG_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__.keys()) | {"message", "asctime"}

_DEFAULT_LOGGING_CONFIG: dict[str, Any] = {
    "level": "INFO",
    "console_enabled": True,
    "file_enabled": True,
    "directory": "storage/logs",
    "filename": "northstar.log",
    "when": "midnight",
    "interval": 1,
    "backup_count": 14,
    "encoding": "utf-8",
    "format": "%(asctime)s | %(levelname)-8s | %(filename)s:%(lineno)d | %(message)s",
}


def _extract_context(record: logging.LogRecord) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _STANDARD_LOG_RECORD_FIELDS and not key.startswith("_")
    }


def _format_timestamp(record: logging.LogRecord) -> str:
    return datetime.fromtimestamp(record.created, tz=timezone.utc).astimezone().isoformat(timespec="milliseconds")


def _render_console_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class ConsoleFormatter(logging.Formatter):
    """控制台输出使用人类可读的管道分隔格式。"""

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        context = _extract_context(record)
        if not context:
            return rendered
        extras = " | ".join(
            f"{key}={_render_console_value(value)}" for key, value in sorted(context.items())
        )
        return f"{rendered} | {extras}"


class JsonLinesFormatter(logging.Formatter):
    """文件日志使用 JSON Lines，一行一条 JSON 记录。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _format_timestamp(record),
            "level": record.levelname,
            "file": record.filename,
            "line": record.lineno,
            "msg": record.getMessage(),
        }
        payload.update(_extract_context(record))

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


class ContextLoggerAdapter(logging.LoggerAdapter):
    """支持 bind 的结构化 logger。"""

    def process(self, msg: Any, kwargs: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        extra = dict(kwargs.get("extra") or {})
        context = dict(self.extra)
        context.update(extra.pop("context", {}) or {})

        filtered_context = {
            key: value
            for key, value in context.items()
            if key not in _STANDARD_LOG_RECORD_FIELDS and not key.startswith("_")
        }
        extra.update(filtered_context)
        kwargs["extra"] = extra
        return msg, kwargs

    def bind(self, **context: Any) -> "ContextLoggerAdapter":
        merged = dict(self.extra)
        merged.update(context)
        return ContextLoggerAdapter(self.logger, merged)


def get_logger(name: str, **context: Any) -> ContextLoggerAdapter:
    """返回支持 bind 的结构化 logger。"""

    return ContextLoggerAdapter(logging.getLogger(name), context)


def _rotation_namer(default_name: str) -> str:
    """把默认滚动文件名改成 northstar-YYYY-MM-DD.log 风格。"""

    path = Path(default_name)
    filename = path.name
    if "." not in filename:
        return default_name

    base_name, suffix = filename.rsplit(".", 1)
    base_path = path.with_name(base_name)
    if base_path.suffix:
        rotated_name = f"{base_path.stem}-{suffix}{base_path.suffix}"
    else:
        rotated_name = f"{base_path.name}-{suffix}"
    return str(path.with_name(rotated_name))


def _load_logging_config(config_path: str | Path = "configs/app.yaml") -> dict[str, Any]:
    """读取日志配置并补齐默认值。"""

    path = Path(config_path)
    if not path.is_absolute():
        path = get_settings().project_root / path
    if not path.exists():
        return dict(_DEFAULT_LOGGING_CONFIG)

    raw_config = load_yaml(path)
    logging_config = raw_config.get("logging", {}) or {}
    merged = {**_DEFAULT_LOGGING_CONFIG, **logging_config}
    merged["level"] = str(merged["level"]).upper()
    merged["interval"] = int(merged["interval"])
    merged["backup_count"] = int(merged["backup_count"])

    if not merged["console_enabled"] and not merged["file_enabled"]:
        merged["console_enabled"] = True

    return merged


def _build_handlers(logging_config: dict[str, Any]) -> list[logging.Handler]:
    """根据配置构建日志 handlers。"""

    handlers: list[logging.Handler] = []

    if logging_config["console_enabled"]:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(ConsoleFormatter(logging_config["format"]))
        handlers.append(console_handler)

    if logging_config["file_enabled"]:
        log_dir = Path(logging_config["directory"])
        if not log_dir.is_absolute():
            log_dir = get_settings().project_root / log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            filename=log_dir / logging_config["filename"],
            when=logging_config["when"],
            interval=logging_config["interval"],
            backupCount=logging_config["backup_count"],
            encoding=logging_config["encoding"],
        )
        file_handler.namer = _rotation_namer
        file_handler.setFormatter(JsonLinesFormatter())
        handlers.append(file_handler)

    return handlers


def setup_logging() -> None:
    """初始化统一日志。"""

    logging_config = _load_logging_config()
    level = getattr(logging, logging_config["level"], logging.INFO)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)

    for handler in _build_handlers(logging_config):
        root_logger.addHandler(handler)

    structlog.configure(
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )
