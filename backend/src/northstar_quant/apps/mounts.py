"""Container admission for every configured source, publication, result and backup mount."""

import os
from pathlib import Path

from northstar_quant.apps.storage import open_database, require_current_database
from northstar_quant.data_management.storage_identity import probe, require_identity


def check() -> None:
    owner = os.environ["NORTHSTAR_DATABASE_OWNER"]
    engine = open_database()
    try:
        require_current_database(engine)
    finally:
        engine.dispose()
    readonly = {"RESEARCH"} if owner == "data_hub" else {"MARKET"}
    for share in ("SOURCE", "MARKET", "RESEARCH", "BACKUP"):
        value = os.environ.get(f"NORTHSTAR_{share}_DIR")
        if value is not None:
            root = Path(value)
            identity = os.environ[f"NORTHSTAR_{share}_STORAGE_ID"]
            require_identity(root, identity)
            if (root / ".restore-incomplete").exists():
                raise ValueError("restore is incomplete; refuse application startup")
            if share not in readonly:
                probe(root, identity)
    print(f"{owner}: database and all share identities verified")


if __name__ == "__main__":
    check()
