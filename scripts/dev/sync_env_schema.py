#!/usr/bin/env python3
"""将唯一活动 `.env` 对齐到 `.env.example` 的完整字段结构。

脚本仅在开发初始化时调用。它保留已有字段的原始值、以示例文件的顺序补齐
缺失字段，并删除不再受支持的字段。若需要改写已有文件，会先在同目录留下
一个受 `.gitignore` 保护的备份；终端输出仅包含文件名和字段名，绝不输出值。
"""

from __future__ import annotations

import argparse
from collections import Counter
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
from uuid import uuid4


_DECLARATION_PATTERN = re.compile(
    r"^(?P<prefix>\s*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)(?P<separator>\s*=)(?P<value>.*)$"
)


class EnvSchemaError(ValueError):
    """环境文件无法安全同步。"""


def _require_unlinked_active_path(active_path: Path) -> None:
    """拒绝活动文件及其父目录中的符号链接。

    活动 `.env` 是唯一允许被本工具改写的非跟踪配置。仅检查末段文件不足以
    防止 ``linked-directory/.env`` 通过父目录链接写到工作区外，因此在每次
    读取或写入活动文件前都检查完整路径链。模板是追踪文件，仍由 CLI 单独
    resolve，以保留已有的模板链接解析语义。
    """

    for candidate in (active_path, *active_path.parents):
        if candidate.is_symlink():
            location = "活动环境文件" if candidate == active_path else "活动环境文件父目录"
            raise EnvSchemaError(
                f"{location}不能是符号链接，已拒绝写入仓库外目标。"
            )


def _read_declarations(path: Path) -> tuple[list[str], dict[str, str], Counter[str]]:
    """读取键名及原始右侧值；不将值打印到 stdout/stderr。"""

    if not path.is_file():
        raise EnvSchemaError(f"文件不存在：{path}")

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    values: dict[str, str] = {}
    counts: Counter[str] = Counter()
    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _DECLARATION_PATTERN.fullmatch(line.rstrip("\r\n"))
        if match is None:
            raise EnvSchemaError(
                f"{path.name} 第 {line_number} 行不是 KEY=value 声明，无法安全迁移。"
            )
        key = match["key"]
        counts[key] += 1
        values[key] = match["value"]

    duplicates = sorted(key for key, count in counts.items() if count > 1)
    if duplicates:
        raise EnvSchemaError(
            f"{path.name} 包含重复字段，无法判定保留哪个值：{', '.join(duplicates)}"
        )
    return lines, values, counts


def _render_with_values(lines: list[str], values: dict[str, str]) -> str:
    rendered: list[str] = []
    for line in lines:
        match = _DECLARATION_PATTERN.fullmatch(line.rstrip("\r\n"))
        if match is None:
            rendered.append(line)
            continue
        key = match["key"]
        value = values.get(key, match["value"])
        rendered.append(f"{match['prefix']}{key}{match['separator']}{value}\n")
    return "".join(rendered)


def _backup_path(active_path: Path) -> Path:
    return active_path.with_name(
        f"{active_path.name}.before-schema-migration-{uuid4().hex}"
    )


def _create_backup(active_path: Path) -> Path:
    """以独占方式保存可恢复副本，绝不覆盖已有备份。"""

    _require_unlinked_active_path(active_path)
    while True:
        backup_path = _backup_path(active_path)
        try:
            with active_path.open("rb") as source, backup_path.open("xb") as destination:
                shutil.copyfileobj(source, destination)
            shutil.copystat(active_path, backup_path)
            return backup_path
        except FileExistsError:
            continue


def _atomic_write(path: Path, content: str, mode: int) -> None:
    _require_unlinked_active_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        # 对 .env 生成 .env.tmp.<随机值>，既与活动文件同目录以保留原子替换，
        # 又受 .gitignore 的 .env.* 规则保护，避免中断时误提交密钥。
        prefix=f"{path.name}.tmp.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def sync_environment_schema(template_path: Path, active_path: Path, *, apply: bool) -> bool:
    """检查或迁移活动文件；返回是否需要/已进行了改写。"""

    _require_unlinked_active_path(active_path)
    template_lines, _, template_counts = _read_declarations(template_path)
    if not template_counts:
        raise EnvSchemaError(f"模板 {template_path.name} 未声明任何环境字段。")

    if active_path.exists():
        _, active_values, active_counts = _read_declarations(active_path)
        previous_content = active_path.read_text(encoding="utf-8")
        mode = stat.S_IMODE(active_path.stat().st_mode)
    else:
        active_values = {}
        active_counts = Counter()
        previous_content = ""
        mode = 0o600

    rendered = _render_with_values(template_lines, active_values)
    expected_keys = set(template_counts)
    active_keys = set(active_counts)
    missing = sorted(expected_keys - active_keys)
    obsolete = sorted(active_keys - expected_keys)
    changed = previous_content != rendered
    if not changed:
        print(f"活动环境文件结构已完整：{active_path.name}（{len(expected_keys)} 个字段）")
        return False

    summary: list[str] = []
    if missing:
        summary.append(f"补齐 {len(missing)} 个字段")
    if obsolete:
        summary.append(f"移除 {len(obsolete)} 个已废弃/未知字段：{', '.join(obsolete)}")
    if not active_path.exists():
        summary.append("创建活动文件")

    if not apply:
        description = "；".join(summary) if summary else "标准化字段顺序和注释"
        raise EnvSchemaError(
            f"活动环境文件需要迁移（{description}）。"
            "请使用 --apply 或重新从 .env.example 创建 .env。"
        )

    if active_path.exists():
        backup_path = _create_backup(active_path)
        print(f"已备份原活动环境文件：{backup_path.name}")
    _atomic_write(active_path, rendered, mode)
    description = "；".join(summary) if summary else "标准化字段顺序和注释"
    print(f"已迁移活动环境文件：{active_path.name}（{description}；共 {len(expected_keys)} 个字段）")
    return True


def _read_updates_from_stdin() -> dict[str, str]:
    """从标准输入读取 `KEY=value` 更新，避免把秘密暴露到进程 argv。"""

    updates: dict[str, str] = {}
    for line_number, line in enumerate(sys.stdin, 1):
        stripped = line.strip()
        if not stripped:
            continue
        match = _DECLARATION_PATTERN.fullmatch(line.rstrip("\r\n"))
        if match is None:
            raise EnvSchemaError(f"标准输入第 {line_number} 行不是 KEY=value 声明。")
        key = match["key"]
        if key in updates:
            raise EnvSchemaError(f"标准输入包含重复字段：{key}")
        updates[key] = match["value"]
    if not updates:
        raise EnvSchemaError("标准输入未提供任何环境变量更新。")
    return updates


def _absolute_path_without_resolving_symlinks(path: Path) -> Path:
    """规范化 CLI 路径，但绝不解析符号链接。

    ``Path.resolve()`` 会把活动 `.env` 本身的链接替换成其目标，导致后续
    ``is_symlink`` 检查失效；``abspath`` 只规范化 ``.`` / ``..`` 路径段。
    """

    return Path(os.path.abspath(path))


def update_environment_values(active_path: Path, updates: dict[str, str]) -> bool:
    """通过 stdin 提供的值原子更新已有字段，不输出任何值。"""

    _require_unlinked_active_path(active_path)
    active_lines, _, active_counts = _read_declarations(active_path)
    missing = sorted(set(updates) - set(active_counts))
    if missing:
        raise EnvSchemaError(
            f"活动环境文件缺少待更新字段：{', '.join(missing)}；请先完成结构迁移。"
        )

    previous_content = "".join(active_lines)
    rendered = _render_with_values(active_lines, updates)
    if previous_content == rendered:
        print(f"活动环境文件无需更新：{active_path.name}")
        return False

    mode = stat.S_IMODE(active_path.stat().st_mode)
    _atomic_write(active_path, rendered, mode)
    print(f"已更新活动环境文件：{active_path.name}（{len(updates)} 个字段）")
    return True


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步活动 .env 的完整字段结构")
    parser.add_argument("--template", type=Path, help="追踪的 .env.example 路径")
    parser.add_argument("--active", type=Path, required=True, help="未跟踪的活动 .env 路径")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际创建/迁移活动文件；省略时仅检查并失败关闭",
    )
    parser.add_argument(
        "--set-stdin",
        action="store_true",
        help="从标准输入读取 KEY=value 并原子更新已有活动文件字段",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        active_path = _absolute_path_without_resolving_symlinks(args.active)
        _require_unlinked_active_path(active_path)
        if args.set_stdin:
            if args.template is not None or args.apply:
                raise EnvSchemaError("--set-stdin 不能与 --template 或 --apply 一起使用。")
            update_environment_values(active_path, _read_updates_from_stdin())
        else:
            if args.template is None:
                raise EnvSchemaError("结构检查或迁移必须提供 --template .env.example。")
            sync_environment_schema(
                args.template.resolve(),
                active_path,
                apply=args.apply,
            )
    except EnvSchemaError as exc:
        print(f"环境文件迁移失败：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
