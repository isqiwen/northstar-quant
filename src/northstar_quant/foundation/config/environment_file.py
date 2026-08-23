"""活动环境文件的结构校验。

`.env.example` 仅用于展示和初始化，运行时绝不能把它当作回退来源。
本模块只检查活动 `.env` 的键名、重复项和字段集合，绝不解析、记录或
在异常中输出变量值，因此可以安全地用于包含凭据的本地文件。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection
from pathlib import Path
import re


# 这五项不属于 Settings，但它们是开发 Docker、隔离测试与 Python 缓存的
# 固定辅助设置。它们和所有可注入的 Settings 字段共同构成完整 .env schema。
ENVIRONMENT_FILE_AUXILIARY_KEYS = frozenset(
    {
        "POSTGRES_PASSWORD",
        "POSTGRES_PORT",
        "NORTHSTAR_TEST_DATABASE_URL",
        "XDG_CACHE_HOME",
        "MPLCONFIGDIR",
    }
)

_DECLARATION_PATTERN = re.compile(
    r"^\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=.*$"
)


class ActiveEnvironmentFileError(ValueError):
    """活动 `.env` 缺失或未遵循完整声明 schema。"""


def declared_environment_key_counts(path: Path) -> Counter[str]:
    """返回活动环境文件的键名及出现次数，不返回任何变量值。"""

    if not path.is_file():
        raise ActiveEnvironmentFileError(
            f"活动环境文件不存在：{path}。请先复制 .env.example 为 .env，"
            "再填写当前环境的值。"
        )

    counts: Counter[str] = Counter()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _DECLARATION_PATTERN.fullmatch(line)
        if match is None:
            raise ActiveEnvironmentFileError(
                f"活动环境文件 {path.name} 第 {line_number} 行不是 KEY=value 声明；"
                "请使用完整的 .env.example 字段结构。"
            )
        counts[match["key"]] += 1
    return counts


def validate_active_environment_file(
    path: Path,
    *,
    expected_keys: Collection[str],
    retired_keys: Collection[str] = (),
) -> None:
    """失败关闭地校验唯一活动 `.env` 的完整字段集合。

    该检查只暴露字段名，便于操作者迁移旧文件而不泄露凭据内容。
    """

    expected = set(expected_keys)
    counts = declared_environment_key_counts(path)
    actual = set(counts)
    duplicates = sorted(key for key, count in counts.items() if count > 1)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    retired = sorted(set(retired_keys) & actual)

    problems: list[str] = []
    if missing:
        problems.append(f"缺少字段：{', '.join(missing)}")
    if duplicates:
        problems.append(f"重复字段：{', '.join(duplicates)}")
    if retired:
        problems.append(f"已废弃字段：{', '.join(retired)}")
    remaining_unexpected = [key for key in unexpected if key not in retired]
    if remaining_unexpected:
        problems.append(f"未知字段：{', '.join(remaining_unexpected)}")

    if problems:
        raise ActiveEnvironmentFileError(
            f"活动环境文件 {path.name} 的字段结构与 .env.example 不一致（"
            + "；".join(problems)
            + "）。请以 .env.example 为完整字段清单迁移该文件；示例文件不会被运行时读取。"
        )
