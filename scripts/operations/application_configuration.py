"""Validate the one current deployment configuration without exposing credential values."""

import re
import runpy
import secrets
from pathlib import Path
from urllib.parse import urlsplit

WORKSPACE_HASH = "NORTHSTAR_WORKSPACE_PASSWORD_HASH"
PASSWORD = "NORTHSTAR_DATA_HUB_DATABASE_PASSWORD"
KEYS = {
    "database": {PASSWORD, "NORTHSTAR_DATABASE_ADMIN_PASSWORD"},
    "data-hub": {PASSWORD},
    "research": {"NORTHSTAR_DATA_HUB_URL"},
    "live": {
        "NORTHSTAR_LIVE_INSTANCES",
        "NORTHSTAR_SIMNOW_USER_ID",
        "NORTHSTAR_SIMNOW_APP_ID",
        "NORTHSTAR_SIMNOW_AUTH_CODE",
        "NORTHSTAR_SIMNOW_PASSWORD",
        "NORTHSTAR_CTP_BROKER_ID",
        "NORTHSTAR_CTP_TRADE_FRONT",
        "NORTHSTAR_CTP_MD_FRONT",
        "NORTHSTAR_CTP_USER_ID",
        "NORTHSTAR_CTP_PASSWORD",
        "NORTHSTAR_CTP_APP_ID",
        "NORTHSTAR_CTP_AUTH_CODE",
    },
}


def validate(app: str, content: bytes) -> dict[str, str]:
    if len(content) > 1024 * 1024:
        raise ValueError("应用配置超过大小限制")
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeError:
        raise ValueError("应用配置必须使用 UTF-8") from None
    values = {}
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=(.*)", line)
        if not match:
            raise ValueError(f"配置第 {number} 行必须是单行 KEY=value")
        key, value = match.groups()
        if key not in KEYS[app] and not (app != "database" and key == WORKSPACE_HASH):
            raise ValueError(f"未知或已废弃的配置参数：{key}")
        if key in values:
            raise ValueError(f"配置参数重复：{key}")
        value = value.strip()
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"配置第 {number} 行引号未闭合")
            quote, value = value[0], value[1:-1]
        else:
            quote = ""
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        if quote != "'" and re.search(r"\$(?:[A-Za-z_{])", value):
            raise ValueError(f"配置参数 {key} 不允许变量展开；字面密码请使用单引号")
        values[key] = value
    missing = KEYS[app] - values.keys()
    if missing:
        raise ValueError("缺少配置参数：" + ", ".join(sorted(missing)))
    required = KEYS[app] - {
        k for k in KEYS[app] if k.startswith(("NORTHSTAR_SIMNOW_", "NORTHSTAR_CTP_"))
    }
    if any(not values[k] for k in required):
        raise ValueError("必填连接或数据库配置为空")
    if app == "research":
        try:
            parsed = urlsplit(values["NORTHSTAR_DATA_HUB_URL"])
            parsed.port
        except ValueError:
            raise ValueError("Data Hub 服务地址或端口无效") from None
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or any(c.isspace() for c in values["NORTHSTAR_DATA_HUB_URL"])
        ):
            raise ValueError("Data Hub 地址必须是无凭据的 HTTP(S) 服务地址")
    if app == "live":
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend/src"))
        from northstar_quant.live.instances import configured_instances

        configured_instances(values["NORTHSTAR_LIVE_INSTANCES"])
        credentials = [values[k] for k in KEYS[app] if k.startswith("NORTHSTAR_SIMNOW_")]
        if any(credentials) and not all(credentials):
            raise ValueError("SimNow 凭据必须全部填写或全部留空")

    if values.get(WORKSPACE_HASH):
        _passwords()["validate_password_hash"](values[WORKSPACE_HASH])
    return values


def _passwords() -> dict:
    return runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "backend/src/northstar_quant/web/passwords.py")
    )


def prepare_workspace_password(
    app: str, content: bytes, existing: bytes | None
) -> tuple[bytes, str | None]:
    """Keep the installed identity unless a replacement hash is explicitly supplied."""
    values = validate(app, content)
    if app == "database" or values.get(WORKSPACE_HASH):
        return content, None
    lines = [
        line
        for line in content.decode().splitlines()
        if not line.strip().startswith(WORKSPACE_HASH + "=")
    ]
    identity = [
        line
        for line in (existing or b"").decode().splitlines()
        if line.strip().startswith(WORKSPACE_HASH + "=")
    ]
    # A valid explicit replacement may remove obsolete settings; retain only its old identity.
    previous = validate(app, ("\n".join(lines + identity) + "\n").encode()).get(WORKSPACE_HASH)
    password = None if previous else secrets.token_urlsafe(24)
    encoded = previous or _passwords()["hash_password"](password)
    lines.append(f"{WORKSPACE_HASH}='{encoded}'")
    return ("\n".join(lines) + "\n").encode(), password
