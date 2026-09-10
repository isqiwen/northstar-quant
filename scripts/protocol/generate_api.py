"""Compile owned Protobuf protocols into Python messages and browser clients."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "backend" / "src"
PROTO = ROOT / "proto"
ROLES = ("data_hub", "research", "live")
PYTHON_MODULES = {
    "api_options": "northstar_quant.web.api_options_pb2",
    "web_auth": "northstar_quant.web.auth_pb2",
    "common": "northstar_quant.web.common_pb2",
    "accounting": "northstar_quant.accounting.protocol_pb2",
    **{role: f"northstar_quant.apps.{role}.api_pb2" for role in ROLES},
}


def place_python_modules(target: Path) -> None:
    """Map protoc's flat Python modules to their owning implementation packages."""
    for source, module in PYTHON_MODULES.items():
        for suffix in (".py", ".pyi"):
            path = target / f"{source}_pb2{suffix}"
            content = path.read_text()
            for dependency, destination in PYTHON_MODULES.items():
                package, name = destination.rsplit(".", 1)
                content = content.replace(
                    f"import {dependency}_pb2 as ", f"from {package} import {name} as "
                )
            content = content.replace(f"'{source}_pb2'", repr(module))
            destination = target.joinpath(*module.split(".")).with_suffix(suffix)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content)
            path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    sources = [PROTO / f"{name}.proto" for name in PYTHON_MODULES]
    with tempfile.TemporaryDirectory(prefix="northstar-protocol-") as temporary:
        target = Path(temporary)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "grpc_tools.protoc",
                f"-I{PROTO}",
                f"--python_out={target}",
                f"--pyi_out={target}",
                *map(str, sources),
            ],
            check=True,
        )
        place_python_modules(target)
        # Import the generated descriptors in an isolated compiler process.
        subprocess.run(
            [sys.executable, str(Path(__file__).with_name("browser.py")), str(target)], check=True
        )
        for role in ROLES:
            subprocess.run(
                [
                    "node",
                    str(ROOT / "frontend/protocol-codec.mjs"),
                    str(target / f"frontend/apps/{role}/api"),
                ],
                check=True,
            )
        stale = []
        for p in sorted(target.rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            relative = p.relative_to(target)
            destination = ROOT / relative if relative.parts[0] == "frontend" else SRC / relative
            content = p.read_bytes()
            if not destination.is_file() or destination.read_bytes() != content:
                stale.append(str(destination.relative_to(ROOT)))
                if not args.check:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(content)
        if args.check and stale:
            raise SystemExit(
                "Protobuf outputs are stale; run npm --prefix frontend run api:generate:\n"
                + "\n".join(stale)
            )


if __name__ == "__main__":
    main()
