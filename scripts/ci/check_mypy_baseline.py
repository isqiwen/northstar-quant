"""验证 Northstar Quant 的 mypy 类型错误基线。

基线不是忽略类型错误的配置：它是当前已知债务的精确、版本化快照。CI 要求实际
诊断与快照一致；Pull Request 还会与目标分支的快照比较，拒绝任何新增诊断并允许
减少。更新快照只能通过显式 ``emit`` 命令生成，再经代码审查提交。
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import TypeAlias

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = PROJECT_ROOT / ".mypy-baseline.json"
SCHEMA_VERSION = 1
TARGETS = ("src/northstar_quant",)
MYPY_ARGUMENTS = (
    "--output=json",
    "--python-version=3.11",
    "--platform=linux",
    "--no-incremental",
    "--no-pretty",
    "--no-color-output",
    "--no-error-summary",
    *TARGETS,
)
_MYPY_VERSION_PATTERN = re.compile(r"^mypy (?P<version>[^ ]+)")
_DIFF_HUNK_PATTERN = re.compile(
    r"^@@ -(?P<base_start>\d+)(?:,(?P<base_count>\d+))? "
    r"\+(?P<current_start>\d+)(?:,(?P<current_count>\d+))? @@"
)
_RENAME_DETECTION = "--find-renames=50%"

Diagnostic = dict[str, int | str | None]
DiagnosticKey: TypeAlias = tuple[str, int, int, str, str]


class BaselineError(RuntimeError):
    """表示基线、类型检查器或 Git 历史不满足契约。"""


@dataclass(frozen=True)
class _LineHunk:
    """Git unified diff 中的一段已改动行区间。"""

    base_start: int
    base_count: int
    current_start: int
    current_count: int


@dataclass(frozen=True)
class _FileLineMap:
    """一个 Git 确认的文件移动及其未修改行坐标映射。"""

    base_path: str
    current_path: str
    hunks: tuple[_LineHunk, ...]

    def map_base_line(self, base_line: int) -> int | None:
        """返回未改动 base 行在当前文件中的行号；改动行返回 ``None``。

        这不是按消息模糊匹配。只有 Git diff 明确认定为未改动的源行才可迁移，
        因而插入/删除导致的行号变化不会被误判为新的类型债务。
        """

        if base_line < 1:
            raise BaselineError(f"mypy 基线包含非法行号：{self.base_path}:{base_line}")

        base_cursor = 1
        current_cursor = 1
        for hunk in self.hunks:
            # Git 对文件起始位置的空区间使用 0；源代码真实行号从 1 开始。
            base_start = max(hunk.base_start, 1)
            current_start = max(hunk.current_start, 1)
            if base_start < base_cursor or current_start < current_cursor:
                raise BaselineError(
                    "Git diff 的 hunk 顺序非法，无法安全映射 mypy 诊断："
                    f"{self.base_path} -> {self.current_path}"
                )

            if base_line < base_start:
                return current_cursor + (base_line - base_cursor)
            if base_line < base_start + hunk.base_count:
                return None

            base_cursor = base_start + hunk.base_count
            current_cursor = current_start + hunk.current_count

        return current_cursor + (base_line - base_cursor)


def _normalize_path(value: str) -> str:
    """将 Windows 与 POSIX 的 mypy 路径统一为仓库相对 POSIX 路径。"""

    normalized = value.replace("\\", "/")
    root_prefix = f"{PROJECT_ROOT.as_posix()}/"
    if normalized.startswith(root_prefix):
        normalized = normalized.removeprefix(root_prefix)
    return normalized


def _normalize_diagnostic(raw: object) -> dict[str, int | str | None]:
    """提取跨平台稳定且足以唯一标识一条 mypy 诊断的字段。"""

    if not isinstance(raw, dict):
        raise BaselineError(f"mypy 输出了非对象 JSON：{raw!r}")

    file_name = raw.get("file")
    message = raw.get("message")
    line = raw.get("line")
    column = raw.get("column")
    code = raw.get("code")
    if not isinstance(file_name, str) or not isinstance(message, str):
        raise BaselineError(f"mypy 诊断缺少 file 或 message：{raw!r}")
    if not isinstance(line, int) or not isinstance(column, int):
        raise BaselineError(f"mypy 诊断缺少 line 或 column：{raw!r}")
    if code is not None and not isinstance(code, str):
        raise BaselineError(f"mypy 诊断 code 非字符串：{raw!r}")

    return {
        "file": _normalize_path(file_name),
        "line": line,
        "column": column,
        "code": code,
        "message": message,
    }


def _diagnostic_key(diagnostic: dict[str, int | str | None]) -> tuple[str, int, int, str, str]:
    line = diagnostic["line"]
    column = diagnostic["column"]
    if not isinstance(line, int) or not isinstance(column, int):
        raise BaselineError(f"规范化诊断缺少整数位置：{diagnostic!r}")
    return (
        str(diagnostic["file"]),
        line,
        column,
        str(diagnostic["code"] or ""),
        str(diagnostic["message"]),
    )


def _sorted_diagnostics(
    diagnostics: list[dict[str, int | str | None]],
) -> list[dict[str, int | str | None]]:
    return sorted(diagnostics, key=_diagnostic_key)


def _mypy_version() -> str:
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--version"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        encoding="utf-8",
        text=True,
    )
    if result.returncode != 0:
        raise BaselineError(f"无法读取 mypy 版本：{result.stderr.strip()}")
    match = _MYPY_VERSION_PATTERN.match(result.stdout.strip())
    if match is None:
        raise BaselineError(f"无法解析 mypy 版本输出：{result.stdout!r}")
    return match.group("version")


def collect_diagnostics() -> tuple[str, list[dict[str, int | str | None]]]:
    """以固定 Python/平台参数收集 mypy JSON 诊断。"""

    result = subprocess.run(
        [sys.executable, "-m", "mypy", *MYPY_ARGUMENTS],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        encoding="utf-8",
        text=True,
    )
    if result.returncode not in {0, 1}:
        details = result.stderr.strip() or result.stdout.strip()
        raise BaselineError(f"mypy 执行失败（退出码 {result.returncode}）：{details}")

    diagnostics: list[dict[str, int | str | None]] = []
    for raw_line in result.stdout.splitlines():
        if raw_line.strip():
            try:
                diagnostics.append(_normalize_diagnostic(json.loads(raw_line)))
            except json.JSONDecodeError as exc:
                raise BaselineError(f"mypy JSON 输出无法解析：{raw_line!r}") from exc

    return _mypy_version(), _sorted_diagnostics(diagnostics)


def _baseline_payload(
    mypy_version: str,
    diagnostics: list[dict[str, int | str | None]],
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mypy_version": mypy_version,
        "arguments": list(MYPY_ARGUMENTS),
        "diagnostics": _sorted_diagnostics(diagnostics),
    }


def _load_baseline_from_text(text: str, *, source: str) -> dict[str, object]:
    try:
        baseline = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BaselineError(f"{source} 不是有效 JSON：{exc}") from exc
    if not isinstance(baseline, dict):
        raise BaselineError(f"{source} 的根节点必须是对象。")
    if baseline.get("schema_version") != SCHEMA_VERSION:
        raise BaselineError(f"{source} 的 schema_version 不受支持。")
    if not isinstance(baseline.get("mypy_version"), str):
        raise BaselineError(f"{source} 缺少 mypy_version。")
    if baseline.get("arguments") != list(MYPY_ARGUMENTS):
        raise BaselineError(f"{source} 的 mypy 参数与检查器不一致。")
    diagnostics = baseline.get("diagnostics")
    if not isinstance(diagnostics, list):
        raise BaselineError(f"{source} 缺少 diagnostics 数组。")

    normalized = [_normalize_diagnostic(item) for item in diagnostics]
    if normalized != _sorted_diagnostics(normalized):
        raise BaselineError(f"{source} 的 diagnostics 必须按稳定顺序保存。")
    baseline["diagnostics"] = normalized
    return baseline


def load_current_baseline() -> dict[str, object]:
    """加载当前工作树中的版本化基线。"""

    if not BASELINE_PATH.is_file():
        raise BaselineError(f"缺少类型基线文件：{BASELINE_PATH.name}")
    return _load_baseline_from_text(
        BASELINE_PATH.read_text(encoding="utf-8"),
        source=BASELINE_PATH.name,
    )


def _diagnostic_counter(
    diagnostics: list[dict[str, int | str | None]],
) -> Counter[tuple[str, int, int, str, str]]:
    return Counter(_diagnostic_key(diagnostic) for diagnostic in diagnostics)


def _format_diagnostic_keys(keys: Counter[tuple[str, int, int, str, str]]) -> str:
    formatted: list[str] = []
    for file_name, line, column, code, message in sorted(keys.elements()):
        suffix = f" [{code}]" if code else ""
        formatted.append(f"  - {file_name}:{line}:{column}: {message}{suffix}")
    return "\n".join(formatted)


def _parse_diff_git_header(line: str) -> tuple[str, str]:
    """解析 ``diff --git`` 头，保留 Git 已识别的旧/新路径。"""

    try:
        paths = shlex.split(line.removeprefix("diff --git "))
    except ValueError as exc:
        raise BaselineError(f"无法解析 Git diff 文件头：{line!r}") from exc
    if len(paths) != 2 or not paths[0].startswith("a/") or not paths[1].startswith("b/"):
        raise BaselineError(f"Git diff 文件头不符合预期：{line!r}")
    return paths[0].removeprefix("a/"), paths[1].removeprefix("b/")


def _parse_patch_path(value: str, *, prefix: str) -> str | None:
    """解析 ``---`` / ``+++`` 头中的路径。"""

    path = value.split("\t", maxsplit=1)[0]
    if path == "/dev/null":
        return None
    if not path.startswith(prefix):
        raise BaselineError(f"Git diff 路径不符合预期：{value!r}")
    return path.removeprefix(prefix)


def _parse_file_line_maps(diff: str) -> dict[str, _FileLineMap]:
    """从 rename-aware、零上下文 Git diff 构造严格的行坐标映射。"""

    maps: dict[str, _FileLineMap] = {}
    base_path: str | None = None
    current_path: str | None = None
    hunks: list[_LineHunk] = []

    def finish_file() -> None:
        if base_path is None or current_path is None:
            return
        if base_path in maps:
            raise BaselineError(f"Git diff 为同一旧路径产生多个映射：{base_path}")
        maps[base_path] = _FileLineMap(
            base_path=base_path,
            current_path=current_path,
            hunks=tuple(hunks),
        )

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            finish_file()
            base_path, current_path = _parse_diff_git_header(line)
            hunks = []
            continue

        if base_path is None and current_path is None:
            continue
        if line.startswith("--- "):
            header_path = _parse_patch_path(line.removeprefix("--- "), prefix="a/")
            if header_path is None:
                base_path = None
            elif header_path != base_path:
                raise BaselineError(f"Git diff 旧路径与文件头不一致：{line!r}")
            continue
        if line.startswith("+++ "):
            header_path = _parse_patch_path(line.removeprefix("+++ "), prefix="b/")
            if header_path is None:
                current_path = None
            elif header_path != current_path:
                raise BaselineError(f"Git diff 新路径与文件头不一致：{line!r}")
            continue

        hunk_match = _DIFF_HUNK_PATTERN.match(line)
        if hunk_match is not None:
            hunks.append(
                _LineHunk(
                    base_start=int(hunk_match.group("base_start")),
                    base_count=int(hunk_match.group("base_count") or "1"),
                    current_start=int(hunk_match.group("current_start")),
                    current_count=int(hunk_match.group("current_count") or "1"),
                )
            )

    finish_file()
    return maps


def _git_diff_file_line_maps(base_revision: str) -> dict[str, _FileLineMap]:
    """读取 base revision 到当前工作树的 Git rename/move 与行映射。"""

    result = subprocess.run(
        [
            "git",
            "diff",
            _RENAME_DETECTION,
            "--no-ext-diff",
            "--unified=0",
            base_revision,
            "--",
            *TARGETS,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        encoding="utf-8",
        text=True,
    )
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip()
        raise BaselineError(f"无法读取 Git 重命名/行映射：{details}")
    return _parse_file_line_maps(result.stdout)


def _source_line_at_base_revision(
    base_revision: str,
    path: str,
    line: int,
    cache: dict[tuple[str, str], tuple[str, ...]],
) -> str:
    cache_key = (base_revision, path)
    if cache_key not in cache:
        result = subprocess.run(
            ["git", "show", f"{base_revision}:{path}"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            check=False,
            encoding="utf-8",
            text=True,
        )
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip()
            raise BaselineError(f"无法读取基线诊断的源码行 {path}：{details}")
        cache[cache_key] = tuple(result.stdout.splitlines())

    lines = cache[cache_key]
    if not 1 <= line <= len(lines):
        raise BaselineError(f"基线诊断行超出源码范围：{path}:{line}")
    return lines[line - 1]


def _source_line_in_worktree(
    path: str,
    line: int,
    cache: dict[str, tuple[str, ...]],
) -> str:
    if path not in cache:
        source_path = PROJECT_ROOT / path
        try:
            cache[path] = tuple(source_path.read_text(encoding="utf-8").splitlines())
        except OSError as exc:
            raise BaselineError(f"无法读取当前诊断的源码行 {path}：{exc}") from exc

    lines = cache[path]
    if not 1 <= line <= len(lines):
        raise BaselineError(f"当前诊断行超出源码范围：{path}:{line}")
    return lines[line - 1]


def _remaining_added_diagnostics_after_migration(
    base_diagnostics: list[Diagnostic],
    current_diagnostics: list[Diagnostic],
    file_line_maps: Mapping[str, _FileLineMap],
    source_line_is_unchanged: Callable[[_FileLineMap, int, int], bool],
) -> Counter[DiagnosticKey]:
    """移除经 Git 路径/行映射确认的旧诊断，返回真正新增的诊断。

    迁移匹配必须同时满足：Git 确认的文件映射、未改动源行映射、原列号、mypy
    error code 和完整消息均一致。任何新文件、改动源码行或语义变化都保留为新增。
    """

    base_remaining = _diagnostic_counter(base_diagnostics) - _diagnostic_counter(current_diagnostics)
    current_remaining = _diagnostic_counter(current_diagnostics) - _diagnostic_counter(base_diagnostics)

    for diagnostic in _sorted_diagnostics(base_diagnostics):
        base_key = _diagnostic_key(diagnostic)
        if base_remaining[base_key] <= 0:
            continue

        base_path = str(diagnostic["file"])
        base_line = diagnostic["line"]
        column = diagnostic["column"]
        if not isinstance(base_line, int) or not isinstance(column, int):
            raise BaselineError(f"规范化诊断缺少整数位置：{diagnostic!r}")

        line_map = file_line_maps.get(base_path)
        if line_map is None:
            continue
        current_line = line_map.map_base_line(base_line)
        if current_line is None:
            continue

        current_key: DiagnosticKey = (
            line_map.current_path,
            current_line,
            column,
            str(diagnostic["code"] or ""),
            str(diagnostic["message"]),
        )
        if current_remaining[current_key] <= 0:
            continue
        if not source_line_is_unchanged(line_map, base_line, current_line):
            continue

        base_remaining[base_key] -= 1
        current_remaining[current_key] -= 1

    return +current_remaining


def _added_diagnostics_against_base(
    base_diagnostics: list[Diagnostic],
    current_diagnostics: list[Diagnostic],
    *,
    base_revision: str,
) -> Counter[DiagnosticKey]:
    """基于 Git 移动与未改动源行，识别相对 base 真正新增的诊断。"""

    file_line_maps = _git_diff_file_line_maps(base_revision)
    base_source_cache: dict[tuple[str, str], tuple[str, ...]] = {}
    current_source_cache: dict[str, tuple[str, ...]] = {}

    def source_line_is_unchanged(
        line_map: _FileLineMap,
        base_line: int,
        current_line: int,
    ) -> bool:
        return _source_line_at_base_revision(
            base_revision,
            line_map.base_path,
            base_line,
            base_source_cache,
        ) == _source_line_in_worktree(
            line_map.current_path,
            current_line,
            current_source_cache,
        )

    return _remaining_added_diagnostics_after_migration(
        base_diagnostics,
        current_diagnostics,
        file_line_maps,
        source_line_is_unchanged,
    )


def check_current_baseline() -> None:
    """要求实际诊断与当前快照完全一致。"""

    baseline = load_current_baseline()
    actual_version, actual_diagnostics = collect_diagnostics()
    baseline_version = baseline["mypy_version"]
    if actual_version != baseline_version:
        raise BaselineError(
            "mypy 版本与基线不一致："
            f"当前 {actual_version}，基线 {baseline_version}。"
        )

    expected_diagnostics = baseline["diagnostics"]
    assert isinstance(expected_diagnostics, list)
    expected = [_normalize_diagnostic(item) for item in expected_diagnostics]
    if actual_diagnostics == expected:
        print(f"mypy 类型基线通过：{len(actual_diagnostics)} 条已记录诊断。")
        return

    added = _diagnostic_counter(actual_diagnostics) - _diagnostic_counter(expected)
    removed = _diagnostic_counter(expected) - _diagnostic_counter(actual_diagnostics)
    details: list[str] = ["mypy 诊断与当前基线不一致；请修复新增问题，或显式运行 emit 更新基线。"]
    if added:
        details.extend(["新增诊断：", _format_diagnostic_keys(added)])
    if removed:
        details.extend(["已移除/变更的基线诊断：", _format_diagnostic_keys(removed)])
    raise BaselineError("\n".join(details))


def check_against_base(base_revision: str) -> None:
    """拒绝相对于目标分支基线新增的诊断，允许减少。"""

    result = subprocess.run(
        ["git", "show", f"{base_revision}:{BASELINE_PATH.name}"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        encoding="utf-8",
        text=True,
    )
    if result.returncode != 0:
        print("目标分支尚无 mypy 基线；本次作为首次基线引入，仅执行当前基线校验。")
        return

    base_baseline = _load_baseline_from_text(result.stdout, source=f"{base_revision}:{BASELINE_PATH.name}")
    current_baseline = load_current_baseline()
    base_diagnostics = base_baseline["diagnostics"]
    current_diagnostics = current_baseline["diagnostics"]
    assert isinstance(base_diagnostics, list)
    assert isinstance(current_diagnostics, list)
    added = _added_diagnostics_against_base(
        [_normalize_diagnostic(item) for item in base_diagnostics],
        [_normalize_diagnostic(item) for item in current_diagnostics],
        base_revision=base_revision,
    )
    if added:
        raise BaselineError(
            "当前分支相对目标分支新增了 mypy 诊断，不能通过更新基线接受：\n"
            + _format_diagnostic_keys(added)
        )
    print("mypy 类型基线增量检查通过：未新增诊断。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="验证 mypy 类型错误基线。")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("check", help="实际诊断必须与当前基线完全一致。")
    emit_parser = subcommands.add_parser("emit", help="把当前诊断输出为候选基线 JSON，不写文件。")
    emit_parser.add_argument("--indent", type=int, default=2, help="JSON 缩进宽度。")
    ratchet_parser = subcommands.add_parser("ratchet", help="拒绝相对目标分支新增的诊断。")
    ratchet_parser.add_argument("--base-revision", required=True, help="目标分支的 Git commit SHA。")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "check":
            check_current_baseline()
        elif args.command == "emit":
            mypy_version, diagnostics = collect_diagnostics()
            print(
                json.dumps(
                    _baseline_payload(mypy_version, diagnostics),
                    ensure_ascii=False,
                    indent=args.indent,
                )
            )
        elif args.command == "ratchet":
            check_against_base(args.base_revision)
        else:  # pragma: no cover - argparse 会保证分支不可达。
            raise BaselineError(f"未知子命令：{args.command}")
    except BaselineError as exc:
        print(f"类型基线检查失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
