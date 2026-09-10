"""Persist bounded, redacted installed-process evidence outside disposable data roots."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def redact(value: str, environment: dict[str, str]) -> str:
    for key, secret in environment.items():
        if len(secret) >= 4 and any(word in key for word in ("PASSWORD", "TOKEN", "AUTH_CODE")):
            value = value.replace(secret, "<REDACTED>")
    return re.sub(r"(postgresql(?:\+psycopg)?://)[^@\s]+@", r"\1<REDACTED>@", value)


def save(directory: Path, executable: str, environment: dict[str, str], status: str) -> None:
    destination = os.environ.get("NORTHSTAR_ACCEPTANCE_ARTIFACTS")
    if not destination:
        return
    target = Path(destination).resolve() / directory.name
    target.mkdir(parents=True, exist_ok=True)
    code = """
import json,sqlite3,sys
from importlib.metadata import version
from sqlalchemy import text
from northstar_quant import code_revision
from northstar_quant.apps.storage import open_database
from northstar_quant.data_management.storage_identity import read_identity
from pathlib import Path
import os
result={'implementation':code_revision(),'package_version':version('northstar-quant'),
        'python':sys.version,'sqlite':sqlite3.sqlite_version,'storage':{}}
engine=open_database()
with engine.connect() as c:
    result['postgresql']=c.execute(text('SELECT version()')).scalar_one()
    result['database']=engine.url.database
engine.dispose()
for name in ('SOURCE','MARKET','RESEARCH'):
    root=os.environ.get('NORTHSTAR_'+name+'_DIR')
    if root:
        result['storage'][name]={'path':root,'uuid':read_identity(Path(root))}
print(json.dumps(result))
"""
    result: dict[str, object] = {
        "status": status,
        "observed_at": datetime.now(UTC).isoformat(),
        "executable": executable,
        "source_sha": os.environ.get("GITHUB_SHA"),
        "evidence_kind": "isolated synthetic installed acceptance; no broker connection",
    }
    try:
        completed = subprocess.run(
            [str(Path(executable).parent / "python"), "-c", code],
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if completed.returncode:
            result["identity_error"] = redact(completed.stderr[-8000:], environment)
        else:
            result["runtime"] = json.loads(completed.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        result["identity_error"] = redact(str(error), environment)
    (target / ("evidence-" + status.replace(" ", "-") + ".json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2)
    )
    for path in directory.rglob("*.log*"):
        if not path.is_file() or path.is_symlink():
            continue
        output = target / path.relative_to(directory)
        output.parent.mkdir(parents=True, exist_ok=True)
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 128 * 1024))
            value = stream.read().decode("utf-8", errors="replace")
        output.write_text(redact(value, environment))
