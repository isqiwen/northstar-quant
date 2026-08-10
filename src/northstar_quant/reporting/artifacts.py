"""报告制品目录的结构化数据读取。"""

from __future__ import annotations

import json
from pathlib import Path

from northstar_quant.common.reporting import REPORT_SCHEMA_VERSION


def load_report_data(report_path: str | Path) -> dict[str, object]:
    """读取报告同目录的 report.json，并校验当前 schema。"""

    data_path = Path(report_path).with_name("report.json")
    if not data_path.is_file():
        raise FileNotFoundError(f"报告缺少结构化数据文件：{data_path}")
    try:
        data = json.loads(data_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"报告结构化数据不是有效 JSON：{data_path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"报告结构化数据顶层必须是对象：{data_path}")
    if data.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise ValueError(f"报告结构化数据 schema 不匹配：{data_path}")
    return data


def report_artifact_label(report_path: str | Path) -> str:
    """返回适合日志和邮件主题使用的完整报告标识。"""

    data = load_report_data(report_path)
    artifact_id = str(data.get("artifact_id") or "").strip()
    if not artifact_id:
        raise ValueError("report.json 缺少 artifact_id")
    return artifact_id.replace("/", "__")
