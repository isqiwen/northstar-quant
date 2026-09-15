"""One durable workspace identity; exclusive publication prevents a second owner."""

import json
import os
import tempfile
from pathlib import Path

from northstar_quant.web.passwords import hash_password, validate_password_hash


class WorkspaceAccount:
    def __init__(self, path: Path):
        self.path = path

    def read(self) -> dict[str, str] | None:
        try:
            value = json.loads(self.path.read_text())
        except FileNotFoundError:
            return None
        if (
            not isinstance(value, dict)
            or set(value) != {"username", "password_hash"}
            or not all(isinstance(v, str) for v in value.values())
        ):
            raise ValueError("工作台账号文件损坏")
        validate_password_hash(value["password_hash"])
        return {"username": str(value["username"]), "password_hash": str(value["password_hash"])}

    def create(self, username: str, password: str) -> None:
        if self.read() is not None:
            raise FileExistsError("工作台账号已创建，请登录。")
        value = {"username": username, "password_hash": hash_password(password)}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(dir=self.path.parent) as temporary:
            temporary.write(json.dumps(value).encode())
            temporary.flush()
            os.fsync(temporary.fileno())
            os.link(temporary.name, self.path)
            descriptor = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
