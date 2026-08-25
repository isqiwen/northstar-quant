"""唯一环境变量示例与本机声明键集合的契约。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re

import pytest

from northstar_quant.foundation.config.environment_file import (
    ENVIRONMENT_FILE_AUXILIARY_KEYS,
    declared_environment_key_counts,
)
from northstar_quant.foundation.config.settings import (
    ENV_DISABLED_FIELDS,
    RETIRED_SIMULATOR_STATE_ENV_VARS,
    Settings,
)
from tests.helpers.paths import PROJECT_ROOT

ENV_TEMPLATE_PATH = PROJECT_ROOT / ".env.example"
ACTIVE_ENV_PATH = PROJECT_ROOT / ".env"
TEMPLATE_VALUE_PATTERN = re.compile(
    r"^\s*(?P<key>[A-Z][A-Z0-9_]*)=(?P<value>.*)$", re.MULTILINE
)
EXPECTED_DISABLED_SETTINGS_FIELDS = frozenset(
    {
        "storage_dir",
        "downloads_dir",
        "reports_dir",
        "log_dir",
    }
)
RETIRED_OUTPUT_ENV_KEYS = frozenset(
    f"NORTHSTAR_{field_name.upper()}" for field_name in EXPECTED_DISABLED_SETTINGS_FIELDS
)
def _declaration_key_counts(path: Path) -> Counter[str]:
    """只提取等号左侧的键名，绝不返回或输出环境变量值。"""

    return declared_environment_key_counts(path)


def _template_values(path: Path) -> dict[str, list[str]]:
    """只用于审计受版本控制的非敏感示例文件。"""

    values: dict[str, list[str]] = {}
    for match in TEMPLATE_VALUE_PATTERN.finditer(path.read_text(encoding="utf-8")):
        values.setdefault(match["key"], []).append(match["value"].strip())
    return values


def _settings_environment_keys() -> set[str]:
    env_prefix = str(Settings.model_config["env_prefix"])
    return {
        f"{env_prefix}{field_name.upper()}"
        for field_name in Settings.model_fields
        if field_name not in ENV_DISABLED_FIELDS
    }


def _assert_template_lists_each_setting_once() -> dict[str, list[str]]:
    declaration_counts = _declaration_key_counts(ENV_TEMPLATE_PATH)
    expected = _settings_environment_keys() | ENVIRONMENT_FILE_AUXILIARY_KEYS

    assert len(expected) == 78
    assert set(declaration_counts) == expected
    for key in expected:
        assert declaration_counts[key] == 1, f".env.example 重复声明 {key}"

    for retired_key in RETIRED_OUTPUT_ENV_KEYS | RETIRED_SIMULATOR_STATE_ENV_VARS:
        assert retired_key not in declaration_counts
    return _template_values(ENV_TEMPLATE_PATH)


def test_env_example_is_the_only_tracked_environment_template() -> None:
    assert ENV_TEMPLATE_PATH.is_file()
    assert not (PROJECT_ROOT / ".env.production.example").exists()


def test_env_example_lists_all_supported_runtime_settings() -> None:
    assert ENV_DISABLED_FIELDS == EXPECTED_DISABLED_SETTINGS_FIELDS
    declarations = _assert_template_lists_each_setting_once()

    assert declarations["POSTGRES_PASSWORD"] == [""]
    assert declarations["POSTGRES_PORT"] == ["5432"]
    assert declarations["NORTHSTAR_TEST_DATABASE_URL"]
    assert declarations["XDG_CACHE_HOME"] == [""]
    assert declarations["MPLCONFIGDIR"] == [""]


def test_active_env_declaration_keys_match_the_example_without_reading_values() -> None:
    if not ACTIVE_ENV_PATH.is_file():
        pytest.skip("本机未创建 .env；CI 不应依赖未跟踪的本地密钥文件")

    # 两侧仅使用等号左侧键名；失败信息也只会显示 Counter 的键名与出现次数。
    assert _declaration_key_counts(ACTIVE_ENV_PATH) == _declaration_key_counts(
        ENV_TEMPLATE_PATH
    )


def test_env_example_keeps_secrets_empty_or_as_explicit_placeholders() -> None:
    declarations = _template_values(ENV_TEMPLATE_PATH)
    for key in (
        "NORTHSTAR_NTFY_BASE_URL",
        "NORTHSTAR_NTFY_TOPIC",
        "NORTHSTAR_NTFY_TOKEN",
        "NORTHSTAR_SMTP_PASSWORD",
    ):
        assert declarations[key] == [""]

    assert "CHANGE_ME" in declarations["NORTHSTAR_DATABASE_URL"][0]


def test_env_example_only_offers_ntfy_for_external_realtime_alerts() -> None:
    declarations = _template_values(ENV_TEMPLATE_PATH)

    assert declarations["NORTHSTAR_ALERT_MODE"] == ["console"]
    assert declarations["NORTHSTAR_NTFY_TIMEOUT_SECONDS"] == ["10"]
    for retired_key in (
        "NORTHSTAR_WECOM_WEBHOOK",
        "NORTHSTAR_WECOM_MENTIONED_MOBILE_LIST",
        "NORTHSTAR_TELEGRAM_BOT_TOKEN",
        "NORTHSTAR_TELEGRAM_CHAT_ID",
        "NORTHSTAR_TELEGRAM_MESSAGE_THREAD_ID",
    ):
        assert retired_key not in declarations


def test_env_example_keeps_live_trading_disabled_by_default() -> None:
    declarations = _template_values(ENV_TEMPLATE_PATH)
    assert declarations["NORTHSTAR_BROKER"] == ["paper"]
    assert declarations["NORTHSTAR_LIVE_TRADING_ENABLED"] == ["false"]
    assert declarations["NORTHSTAR_KILL_SWITCH_ENABLED"] == ["false"]


def test_env_example_does_not_offer_retired_output_path_environment_variables() -> None:
    declaration_counts = _declaration_key_counts(ENV_TEMPLATE_PATH)
    for key in RETIRED_OUTPUT_ENV_KEYS | RETIRED_SIMULATOR_STATE_ENV_VARS:
        assert key not in declaration_counts
