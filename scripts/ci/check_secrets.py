"""Fail a repository check when tracked source files contain likely real secrets."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


_SENSITIVE_NAME = (
    r"authorization|api[_ -]?key|credential|"
    r"(?:access|refresh|id)?[_ -]?token|"
    r"(?:client[_ -]?)?secret|password|passwd|cookie"
)
_KEY_VALUE = re.compile(
    rf"(?im)(?P<key>\b(?:[A-Za-z][A-Za-z0-9_.-]*[_-])?(?:{_SENSITIVE_NAME})\b)\s*[:=]\s*"
    r"(?P<value>[^\s#]+)"
)
_URL_USERINFO = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^:/\s]+:(?P<value>[^@/\s]+)@")
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")
_AWS_ACCESS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_GITHUB_TOKEN = re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")
_SLACK_TOKEN = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")
_AUTH_HEADER = re.compile(r"(?i)\bauthorization\s*:\s*(?:bearer|basic)\s+([^\s]+)")
_ENVIRONMENT_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
_PYTHON_NAME_OR_TYPE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*(?:\[[^]]+\])?(?:\s*\|\s*None)?$"
)
_ALLOW_DIRECTIVE_PATTERN = re.compile(
    r"(?i)#\s*secret-scan\s*:\s*allow\b(?P<suffix>[^\r\n]*)"
)
_ALLOW_REASON_PATTERN = re.compile(r"(?is)^;\s*reason\s*:\s*(?P<reason>.+?)\s*$")
_DISPOSABLE_FIXTURE_PREFIXES = ("tests/", "scripts/ci/fixtures/")
_DISPOSABLE_CI_FIXTURES = frozenset({".github/workflows/ci.yml"})
_BINARY_MAGIC_PREFIXES = (
    b"%PDF-",
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"\x1f\x8b",
    b"7z\xbc\xaf'\x1c",
)
_EXPLICIT_PLACEHOLDERS = frozenset({"", "change_me", "placeholder"})
_ENVIRONMENT_PLACEHOLDER = re.compile(
    r"^\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::\?(?P=name) is required)?\}$"
)
_SHELL_PLACEHOLDER = re.compile(r"^\$(?:[1-9][0-9]*|[A-Za-z_][A-Za-z0-9_]*)$")
_TEMPLATE_PLACEHOLDER = re.compile(r"^(?:<[^<>\r\n]+>|\{\{[^{}\r\n]+\}\}|%[^%\r\n]+%)$")
_CHANGE_ME_MARKER = re.compile(r"(?i)(?<![A-Za-z0-9])change_me(?![A-Za-z0-9])")
_PYTHON_CALL_EXPRESSION = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*\(")
_PYTHON_EXPRESSION = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:(?:\([^{}\r\n]*\)|\.[A-Za-z_][A-Za-z0-9_]*))*$"
)
_SAFE_ENV_READ_CALL = re.compile(r'^_read_env_value\("[A-Z][A-Z0-9_]*"\)$')
_MAPPING_KEY_LITERAL = re.compile(r"\[[\"'][^\"'\r\n]+[\"']\]")
_PYTHON_INTERPOLATION = re.compile(r"^\{(?P<expression>[^{}\r\n]+)\}$")
_PYTHON_FSTRING_FRAGMENT = re.compile(
    r"(?i)\b(?:fr|rf|f)(?P<quote>[\"'])(?P<body>.*?)(?P=quote)"
)
_SHELL_COMMAND_SUBSTITUTION = re.compile(
    r"^[\"']?\$\((?P<command>[^()\r\n]*)\)[\"']?(?:[,;)]*)$"
)
_SAFE_SHELL_COMMANDS = (
    re.compile(r"^openssl\s+rand\s+-base64\s+[1-9][0-9]*$"),
    re.compile(
        r'^deploy_read_env_value\s+"\$\{[A-Z][A-Z0-9_]*\}"\s+"[A-Z][A-Z0-9_]*"$'
    ),
)
_SAFE_OWNER_TOKEN_FSTRING = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*-$")


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().strip("'\"").lower()
    return (
        normalized in _EXPLICIT_PLACEHOLDERS
        or _CHANGE_ME_MARKER.search(normalized) is not None
        or _ENVIRONMENT_PLACEHOLDER.fullmatch(normalized) is not None
        or _SHELL_PLACEHOLDER.fullmatch(normalized) is not None
        or _TEMPLATE_PLACEHOLDER.fullmatch(normalized) is not None
    )


def _is_dynamic_expression(*, key: str, value: str) -> bool:
    candidate = value.strip().rstrip(",;")
    interpolation = _PYTHON_INTERPOLATION.fullmatch(candidate.strip("'\""))
    return (
        _is_safe_python_call(candidate)
        or (
            interpolation is not None
            and _is_safe_python_expression(interpolation.group("expression"))
        )
        or _is_safe_dynamic_fstring(key=key, value=candidate)
    )


def _is_safe_python_call(value: str) -> bool:
    if _SAFE_ENV_READ_CALL.fullmatch(value) is not None:
        return True
    if _PYTHON_CALL_EXPRESSION.match(value) is None:
        return False
    without_mapping_keys = _MAPPING_KEY_LITERAL.sub("", value)
    return "'" not in without_mapping_keys and '"' not in without_mapping_keys


def _is_safe_python_expression(value: str) -> bool:
    if _PYTHON_EXPRESSION.fullmatch(value) is None:
        return False
    without_mapping_keys = _MAPPING_KEY_LITERAL.sub("", value)
    return "'" not in without_mapping_keys and '"' not in without_mapping_keys


def _is_safe_dynamic_fstring(*, key: str, value: str) -> bool:
    fragment = _PYTHON_FSTRING_FRAGMENT.fullmatch(value)
    if fragment is None:
        return False
    return _is_safe_fstring_value(key=key, value=fragment.group("body"))


def _is_safe_fstring_value(*, key: str, value: str) -> bool:
    expressions = list(re.finditer(r"\{([^{}\r\n]+)\}", value))
    if not expressions or any(
        not _is_safe_python_expression(match.group(1)) for match in expressions
    ):
        return False

    static_parts: list[str] = []
    cursor = 0
    for expression in expressions:
        static_parts.append(value[cursor : expression.start()])
        cursor = expression.end()
    static_parts.append(value[cursor:])
    static = "".join(static_parts)
    static_without_escapes = re.sub(r"\\[\\'\"abfnrtv0]", "", static)
    if not static_without_escapes:
        return True
    return key.lower() == "owner_token" and _SAFE_OWNER_TOKEN_FSTRING.fullmatch(
        static_without_escapes
    ) is not None


def _is_safe_fstring_assignment(line: str, match: re.Match[str]) -> bool:
    for fragment in _PYTHON_FSTRING_FRAGMENT.finditer(line):
        if fragment.start() <= match.start() < fragment.end():
            offset = match.start("value") - fragment.start("body")
            if offset < 0:
                return False
            return _is_safe_fstring_value(
                key=match.group("key"), value=fragment.group("body")[offset:]
            )
    return False


def _is_environment_value_expression(line: str, match: re.Match[str]) -> bool:
    value = line[match.start("value") :].strip()
    return _ENVIRONMENT_PLACEHOLDER.fullmatch(value) is not None


def _is_safe_shell_command_substitution(line: str, match: re.Match[str]) -> bool:
    value = line[match.start("value") :].strip()
    substitution = _SHELL_COMMAND_SUBSTITUTION.fullmatch(value)
    if substitution is None:
        return False
    command = substitution.group("command").strip()
    return any(pattern.fullmatch(command) is not None for pattern in _SAFE_SHELL_COMMANDS)


def _is_likely_real_value(*, key: str, value: str) -> bool:
    candidate = value.strip()
    if _is_placeholder(candidate) or _is_dynamic_expression(key=key, value=candidate):
        return False
    normalized = candidate.rstrip(",;)]}")
    if _is_placeholder(normalized):
        return False
    if normalized.startswith(("'", '"')):
        return True
    if _ENVIRONMENT_KEY.fullmatch(key):
        return True
    return len(normalized) >= 12 and _PYTHON_NAME_OR_TYPE.fullmatch(normalized) is None


def _normalize_relative_path(relative_path: Path | str | None) -> str:
    if relative_path is None:
        return ""
    normalized = str(relative_path)
    if not normalized or "\\" in normalized or normalized.startswith("/"):
        return ""
    if re.match(r"^[A-Za-z]:", normalized) is not None:
        return ""
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return ""
    return normalized


def _is_disposable_fixture_path(relative_path: Path | str | None) -> bool:
    normalized = _normalize_relative_path(relative_path)
    return normalized in _DISPOSABLE_CI_FIXTURES or normalized.startswith(
        _DISPOSABLE_FIXTURE_PREFIXES
    )


def _is_binary_blob(path: Path) -> bool:
    with path.open("rb") as stream:
        prefix = stream.read(max(len(item) for item in _BINARY_MAGIC_PREFIXES))
    return any(prefix.startswith(item) for item in _BINARY_MAGIC_PREFIXES)


def _repository_root(start_path: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(start_path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(result.stdout.strip())


def _allow_directive_reason(line: str) -> str | None:
    """Return a directive reason, or an empty string for an invalid directive."""

    match = _ALLOW_DIRECTIVE_PATTERN.search(line)
    if match is None:
        return None

    reason_match = _ALLOW_REASON_PATTERN.fullmatch(match.group("suffix"))
    if reason_match is None:
        return ""

    reason = reason_match.group("reason").strip()
    if not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", reason):
        return ""
    return reason


def find_secret_lines(
    text: str, *, relative_path: Path | str | None = None
) -> list[int]:
    """Return one-based lines containing a likely committed credential.

    The check is deliberately conservative: templates and explicitly annotated
    non-secret fixtures may remain readable, but concrete key-value credentials,
    DSNs, private keys and common cloud/chat tokens fail the repository gate.
    An allow directive is fail-closed unless it has a reason and appears in a
    disposable test or CI fixture path.
    """

    findings: list[int] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        directive_reason = _allow_directive_reason(line)
        if directive_reason is not None:
            if directive_reason and _is_disposable_fixture_path(relative_path):
                continue
            findings.append(line_number)
            continue
        key_value_finding = any(
            not _is_environment_value_expression(line, match)
            and not _is_safe_shell_command_substitution(line, match)
            and not _is_safe_fstring_assignment(line, match)
            and _is_likely_real_value(key=match.group("key"), value=match.group("value"))
            for match in _KEY_VALUE.finditer(line)
        )
        url_userinfo_finding = any(
            not _is_dynamic_expression(key="url_credential", value=match.group("value"))
            and not _is_placeholder(match.group("value"))
            for match in _URL_USERINFO.finditer(line)
        )
        authorization_header_finding = any(
            not _is_dynamic_expression(key="authorization", value=match.group(1))
            and not _is_placeholder(match.group(1))
            for match in _AUTH_HEADER.finditer(line)
        )
        if (
            key_value_finding
            or url_userinfo_finding
            or _PRIVATE_KEY.search(line) is not None
            or _AWS_ACCESS_KEY.search(line) is not None
            or _GITHUB_TOKEN.search(line) is not None
            or _SLACK_TOKEN.search(line) is not None
            or authorization_header_finding
        ):
            findings.append(line_number)
    return findings


def find_secret_paths(root: Path) -> list[Path]:
    repository_root = _repository_root(root)
    tracked = subprocess.run(
        ["git", "-C", str(repository_root), "ls-files", "-z"],
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")
    findings: list[Path] = []
    for raw_relative in tracked:
        if not raw_relative:
            continue
        try:
            relative = raw_relative.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(Path(f"<invalid-utf8-path-{raw_relative.hex()}>"))
            continue
        path = repository_root / relative
        relative_path = Path(relative)
        if path.is_symlink():
            findings.append(relative_path)
            continue
        if not path.is_file():
            continue
        try:
            if _is_binary_blob(path):
                continue
            if find_secret_lines(
                path.read_text(encoding="utf-8"), relative_path=relative
            ):
                findings.append(relative_path)
        except (OSError, UnicodeDecodeError):
            findings.append(relative_path)
    return findings


def main() -> int:
    findings = find_secret_paths(Path.cwd())
    if findings:
        print("SECRET_SCAN_FAILED: " + ", ".join(str(path) for path in findings), file=sys.stderr)
        return 1
    print("secret scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
