"""环境变量示例必须完整、可审计且保持安全默认值。"""

from __future__ import annotations

from pathlib import Path
import re

from northstar_quant.config.settings import ENV_DISABLED_FIELDS, Settings
from tests.support.paths import PROJECT_ROOT

DEVELOPMENT_TEMPLATE_PATH = PROJECT_ROOT / ".env.example"
PRODUCTION_TEMPLATE_PATH = PROJECT_ROOT / ".env.production.example"
DECLARATION_PATTERN = re.compile(
    r"^\s*(?:#\s*)?(?P<key>[A-Z][A-Z0-9_]*)=(?P<value>.*)$", re.MULTILINE
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


def _declarations(path: Path) -> dict[str, list[str]]:
    declarations: dict[str, list[str]] = {}
    for match in DECLARATION_PATTERN.finditer(path.read_text(encoding="utf-8")):
        declarations.setdefault(match["key"], []).append(match["value"].strip())
    return declarations


def _settings_environment_keys() -> set[str]:
    env_prefix = str(Settings.model_config["env_prefix"])
    return {
        f"{env_prefix}{field_name.upper()}"
        for field_name in Settings.model_fields
        if field_name not in ENV_DISABLED_FIELDS
    }


def _assert_template_lists_each_setting_once(
    path: Path, *, allowed_non_settings_keys: set[str]
) -> dict[str, list[str]]:
    declarations = _declarations(path)
    northstar_keys = {
        key for key in declarations if key.startswith("NORTHSTAR_")
    }
    expected = _settings_environment_keys() | allowed_non_settings_keys

    assert northstar_keys == expected
    for key in expected:
        assert len(declarations[key]) == 1, f"{path.name} 重复声明 {key}"

    for retired_key in RETIRED_OUTPUT_ENV_KEYS:
        assert retired_key not in declarations
    return declarations


def test_development_env_template_lists_all_runtime_settings() -> None:
    assert ENV_DISABLED_FIELDS == EXPECTED_DISABLED_SETTINGS_FIELDS
    declarations = _assert_template_lists_each_setting_once(
        DEVELOPMENT_TEMPLATE_PATH,
        allowed_non_settings_keys={"NORTHSTAR_TEST_DATABASE_URL"},
    )

    assert declarations["POSTGRES_PASSWORD"] == [""]
    assert declarations["POSTGRES_PORT"] == ["5432"]
    assert declarations["NORTHSTAR_TEST_DATABASE_URL"]
    assert declarations["XDG_CACHE_HOME"]
    assert declarations["MPLCONFIGDIR"]


def test_production_env_template_lists_all_runtime_settings_without_test_database() -> None:
    assert ENV_DISABLED_FIELDS == EXPECTED_DISABLED_SETTINGS_FIELDS
    declarations = _assert_template_lists_each_setting_once(
        PRODUCTION_TEMPLATE_PATH,
        allowed_non_settings_keys=set(),
    )

    assert "NORTHSTAR_TEST_DATABASE_URL" not in declarations
    assert "POSTGRES_PASSWORD" not in declarations
    assert "POSTGRES_PORT" not in declarations
    assert declarations["XDG_CACHE_HOME"]
    assert declarations["MPLCONFIGDIR"]


def test_env_templates_keep_secrets_empty_or_as_explicit_placeholders() -> None:
    for path in (DEVELOPMENT_TEMPLATE_PATH, PRODUCTION_TEMPLATE_PATH):
        declarations = _declarations(path)
        for key in (
            "NORTHSTAR_WECOM_WEBHOOK",
            "NORTHSTAR_TELEGRAM_BOT_TOKEN",
            "NORTHSTAR_TELEGRAM_CHAT_ID",
            "NORTHSTAR_SMTP_PASSWORD",
        ):
            assert declarations[key] == [""]

    development = _declarations(DEVELOPMENT_TEMPLATE_PATH)
    production = _declarations(PRODUCTION_TEMPLATE_PATH)
    assert "@127.0.0.1" in development["NORTHSTAR_DATABASE_URL"][0]
    assert "CHANGE_ME" in production["NORTHSTAR_DATABASE_URL"][0]


def test_env_templates_keep_live_trading_disabled_by_default() -> None:
    for path in (DEVELOPMENT_TEMPLATE_PATH, PRODUCTION_TEMPLATE_PATH):
        declarations = _declarations(path)
        assert declarations["NORTHSTAR_BROKER"] == ["paper"]
        assert declarations["NORTHSTAR_LIVE_TRADING_ENABLED"] == ["false"]
        assert declarations["NORTHSTAR_KILL_SWITCH_ENABLED"] == ["false"]


def test_env_templates_do_not_offer_retired_output_path_environment_variables() -> None:
    for path in (DEVELOPMENT_TEMPLATE_PATH, PRODUCTION_TEMPLATE_PATH):
        content = path.read_text(encoding="utf-8")
        for key in RETIRED_OUTPUT_ENV_KEYS:
            assert not re.search(rf"^\s*(?:#\s*)?{key}\s*=", content, re.MULTILINE)
