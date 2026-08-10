"""供本地 Dashboard 发现可验证报告制品的只读目录工具。"""

from __future__ import annotations

import json
from pathlib import Path

from northstar_quant.common.reporting import REPORT_SCHEMA_VERSION


def list_recent_report_artifacts(
    report_root: str | Path,
    *,
    limit: int = 20,
) -> list[Path]:
    """列出目录树中可验证的正式报告，按修改时间稳定倒序排列。

    Dashboard 不应把临时 Markdown、手工笔记或不完整的制品目录误认为报告。
    因此只接受同时具备 ``report.md``、符合当前 schema 的 ``report.json``，且
    ``artifact_id`` 与相对目录一致的制品。读取异常或正在写入的不完整制品会被
    忽略，下一次刷新后再尝试发现。

    此处只做只读索引，不能依赖 ``reporting`` 包：报告生成器本身会读取运行健康
    信息，反向导入会形成包级循环依赖。
    """

    if limit <= 0:
        return []

    root = Path(report_root)
    if not root.is_dir():
        return []

    candidates: list[tuple[int, str, Path]] = []
    for markdown_path in root.rglob("report.md"):
        if not markdown_path.is_file() or markdown_path.is_symlink():
            continue
        try:
            relative_directory = markdown_path.parent.relative_to(root).as_posix()
            if relative_directory in {"", "."}:
                continue
            if not _is_canonical_report_artifact(markdown_path, relative_directory):
                continue
            modified_at_ns = markdown_path.stat().st_mtime_ns
        except OSError:
            continue
        candidates.append((modified_at_ns, relative_directory, markdown_path))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [markdown_path for _, _, markdown_path in candidates[:limit]]


def _is_canonical_report_artifact(markdown_path: Path, artifact_id: str) -> bool:
    """验证 Dashboard 所需的最小正式制品身份，不传播损坏文件异常。"""

    data_path = markdown_path.with_name("report.json")
    if not data_path.is_file() or data_path.is_symlink():
        return False
    try:
        payload = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("schema_version") == REPORT_SCHEMA_VERSION
        and payload.get("artifact_id") == artifact_id
    )
