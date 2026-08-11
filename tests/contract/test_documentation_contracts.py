"""关键文档与实际工程边界的一致性契约。"""

from __future__ import annotations

import ast
from pathlib import Path
import re

from northstar_quant.config.settings import Settings
from tests.support.paths import PROJECT_ROOT

README_PATH = PROJECT_ROOT / "README.md"
DOCS_INDEX_PATH = PROJECT_ROOT / "docs" / "README.md"
ROADMAP_PATH = PROJECT_ROOT / "docs" / "08_项目主规划与实施状态.md"
ADMISSION_POLICY_PATH = PROJECT_ROOT / "docs" / "09_研究准入政策与数据治理.md"
CONFIG_GUIDE_PATH = PROJECT_ROOT / "docs" / "02_配置说明.md"
TUTORIAL_PATH = PROJECT_ROOT / "docs" / "00_第一个策略与回测教程.md"
LOCAL_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
NUMBERED_DOCUMENT_PATTERN = re.compile(r"^(\d{2})_.+\.md$")

CANONICAL_DOCUMENTS = (
    "00_第一个策略与回测教程.md",
    "01_架构总览.md",
    "02_配置说明.md",
    "03_执行与安全边界.md",
    "04_期货回测器说明.md",
    "05_报告_PDF与通知.md",
    "06_代码与配置注释规范.md",
    "07_Linux一键部署.md",
    "08_项目主规划与实施状态.md",
    "09_研究准入政策与数据治理.md",
)

RETIRED_DUPLICATE_DOCUMENTS = (
    "03_模块设计说明.md",
    "05_限价执行_超时撤单_交易日历与Dashboard.md",
    "06_限价单追价执行器.md",
    "08_邮件附件PDF报告.md",
    "09_正式版PDF报告版式.md",
    "10_架构审核与演进路线.md",
    "13_审计修复与上线门槛.md",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _fenced_python_blocks(markdown: str) -> list[str]:
    return re.findall(r"```python\n(.*?)\n```", markdown, flags=re.DOTALL)


def _markdown_files() -> list[Path]:
    return sorted(
        path
        for path in PROJECT_ROOT.rglob("*.md")
        if ".venv" not in path.parts and ".git" not in path.parts
    )


def _local_link_target(markdown_path: Path, raw_target: str) -> Path | None:
    target = raw_target.split("#", maxsplit=1)[0].strip().strip("<>")
    if not target or target.startswith(("https://", "http://", "mailto:")):
        return None
    return (markdown_path.parent / target).resolve()


def test_docs_index_is_the_only_canonical_navigation() -> None:
    readme = _read(README_PATH)
    docs_index = _read(DOCS_INDEX_PATH)

    assert "[文档导航](docs/README.md)" in readme
    for filename in CANONICAL_DOCUMENTS:
        assert (DOCS_INDEX_PATH.parent / filename).is_file()
        assert f"]({filename})" in docs_index
    for filename in RETIRED_DUPLICATE_DOCUMENTS:
        assert not (DOCS_INDEX_PATH.parent / filename).exists()


def test_numbered_docs_are_continuous() -> None:
    numbered_documents = sorted(
        path.name
        for path in DOCS_INDEX_PATH.parent.glob("*.md")
        if NUMBERED_DOCUMENT_PATTERN.fullmatch(path.name)
    )

    assert numbered_documents == list(CANONICAL_DOCUMENTS)
    assert [filename[:2] for filename in numbered_documents] == [
        f"{index:02d}" for index in range(len(CANONICAL_DOCUMENTS))
    ]


def test_local_markdown_links_resolve() -> None:
    broken_links: list[str] = []
    for markdown_path in _markdown_files():
        for raw_target in LOCAL_LINK_PATTERN.findall(_read(markdown_path)):
            target = _local_link_target(markdown_path, raw_target)
            if target is not None and not target.exists():
                broken_links.append(f"{markdown_path.relative_to(PROJECT_ROOT)} -> {raw_target}")

    assert not broken_links, "失效的仓库内 Markdown 链接：\n" + "\n".join(broken_links)


def test_roadmap_is_linked_and_preserves_all_phase_gates() -> None:
    readme = _read(README_PATH)
    roadmap = _read(ROADMAP_PATH)

    assert "[项目主规划与实施状态](docs/08_项目主规划与实施状态.md)" in readme
    for phase in range(8):
        assert f"### P{phase}：" in roadmap
    assert "AI 实施协议" in roadmap
    assert "P0-01 至 P0-06" in roadmap
    for filename in (
        "01_架构总览.md",
        "03_执行与安全边界.md",
        "04_期货回测器说明.md",
        "09_研究准入政策与数据治理.md",
    ):
        assert (ROADMAP_PATH.parent / filename).is_file()


def test_research_admission_policy_is_linked_and_keeps_fail_closed_boundaries() -> None:
    readme = _read(README_PATH)
    policy = _read(ADMISSION_POLICY_PATH)

    assert "(docs/09_研究准入政策与数据治理.md)" in readme
    assert "procurement_pending" in policy
    assert "pending_owner_approval" in policy
    assert "不适用范围：模拟交易授权、真实 CTP、真实资金" in policy
    assert (PROJECT_ROOT / "configs" / "data" / "sources.yaml").is_file()
    assert (
        PROJECT_ROOT
        / "configs"
        / "research"
        / "admission"
        / "cn_commodity_futures_research_conservative_v1.yaml"
    ).is_file()


def test_configuration_documentation_matches_safe_runtime_defaults() -> None:
    config_guide = _read(CONFIG_GUIDE_PATH)

    assert "setup_dev.ps1" in config_guide
    assert "开发环境只支持 macOS 或 Linux" not in config_guide
    assert "configs/data/sources.yaml" in config_guide
    assert "research_admission" in config_guide
    assert "ctp_sim" in config_guide
    assert "runtime.downloads_dir" in config_guide
    assert "runtime.log_dir" in config_guide
    assert "configs/app.example.yaml" in config_guide
    assert "configs/app.yaml" in config_guide
    assert "configs/app.local.yaml" in config_guide
    assert "不再被支持" in config_guide
    assert "northstar data cleanup" in config_guide
    assert "标准市场数据" in config_guide
    assert "PostgreSQL 服务的独立数据卷" in config_guide
    assert (PROJECT_ROOT / "configs" / "maintenance" / "output_retention.yaml").is_file()
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
