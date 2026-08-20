"""完整示例与唯一活动应用配置的一致性契约。"""

from __future__ import annotations

from typing import Any

from northstar_quant.platform.config.app_runtime import load_app_config
from northstar_quant.platform.config.yaml_loader import load_yaml
from tests.helpers.paths import PROJECT_ROOT


def _key_shape(value: Any) -> object:
    """仅比较字段层级，不限制允许因环境而不同的标量值。"""

    if isinstance(value, dict):
        return {str(key): _key_shape(child) for key, child in sorted(value.items())}
    if isinstance(value, list):
        return [_key_shape(child) for child in value]
    return None


def test_example_and_active_app_config_have_identical_complete_schema() -> None:
    example_path = PROJECT_ROOT / "configs" / "app.example.yaml"
    active_path = PROJECT_ROOT / "configs" / "app.yaml"

    assert example_path.is_file()
    assert active_path.is_file(), (
        "缺少唯一活动配置 configs/app.yaml；请先运行 just dev-setup 或 "
        "uv run python scripts/dev/setup.py --initialize-config，或从 "
        "configs/app.example.yaml 复制创建。"
    )

    example = load_yaml(example_path)
    active = load_yaml(active_path)

    assert _key_shape(active) == _key_shape(example)
    load_app_config(PROJECT_ROOT)
