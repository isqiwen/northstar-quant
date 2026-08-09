"""关键操作文档与实际工程边界的一致性契约。"""

from __future__ import annotations

import ast
from pathlib import Path
import re

from northstar_quant.config.settings import Settings
from tests.support.paths import PROJECT_ROOT

README_PATH = PROJECT_ROOT / "README.md"
ROADMAP_PATH = PROJECT_ROOT / "docs" / "15_项目主规划与实施状态.md"
CONFIG_GUIDE_PATH = PROJECT_ROOT / "docs" / "02_配置说明.md"
TUTORIAL_PATH = PROJECT_ROOT / "docs" / "00_第一个策略与回测教程.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _fenced_python_blocks(markdown: str) -> list[str]:
    return re.findall(r"```python\n(.*?)\n```", markdown, flags=re.DOTALL)


def test_roadmap_is_linked_and_preserves_all_phase_gates() -> None:
    readme = _read(README_PATH)
    roadmap = _read(ROADMAP_PATH)

    assert "[项目主规划与实施状态](docs/15_项目主规划与实施状态.md)" in readme
    for phase in range(8):
        assert f"### P{phase}：" in roadmap
    assert "AI 实施协议" in roadmap
    assert "P0-01 至 P0-06" in roadmap
    for filename in (
        "10_架构审核与演进路线.md",
        "12_期货回测器说明.md",
        "13_审计修复与上线门槛.md",
    ):
        assert (ROADMAP_PATH.parent / filename).is_file()


def test_configuration_documentation_matches_safe_runtime_defaults() -> None:
    config_guide = _read(CONFIG_GUIDE_PATH)

    assert "scripts/setup_dev.ps1" in config_guide
    assert "开发环境只支持 macOS 和 Linux" not in config_guide
    assert Settings.model_fields["broker"].default == "paper"
    assert Settings.model_fields["live_trading_enabled"].default is False
    assert Settings.model_fields["kill_switch_enabled"].default is False
    assert not (PROJECT_ROOT / "configs" / "risk" / "global.yaml").exists()
    assert not (PROJECT_ROOT / "configs" / "portfolio" / "multi_strategy.yaml").exists()
    assert "已被移除" in config_guide


def test_tutorial_python_examples_remain_parseable_and_describe_explicit_flat_targets() -> None:
    tutorial = _read(TUTORIAL_PATH)
    python_blocks = _fenced_python_blocks(tutorial)

    assert python_blocks
    for index, source in enumerate(python_blocks, start=1):
        ast.parse(source, filename=f"{TUTORIAL_PATH.name}:python-block-{index}")
    assert "target_weight: 0.0" in tutorial
    assert "uv run northstar backtest run first_breakout" in tutorial
