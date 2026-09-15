"""Longer synthetic train/validation/test inputs through the installed data owner."""

import base64
from datetime import datetime, timedelta
from uuid import uuid4

from support.processes import InstalledApplication


def seed_learning(app: InstalledApplication, spec: dict, archive: dict) -> list[str]:
    snapshots = []
    for day in (3, 4, 5):
        start = datetime.fromisoformat(spec["session_open"]) + timedelta(days=day)
        fixed = {
            **spec,
            "trading_day": start.date().isoformat(),
            "session_open": start.isoformat().replace("+00:00", "Z"),
            "session_close": (start + timedelta(minutes=40)).isoformat().replace("+00:00", "Z"),
        }
        content = "event_time,available_at,source_record_id,open,high,low,close,volume\n"
        for i in range(40):
            at = start + timedelta(minutes=i)
            stamp = at.isoformat().replace("+00:00", "Z")
            completed = (at + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
            price = 3100 + (i * i + 7 * i) % 41
            content += f"{stamp},{completed},r{i},{price},{price + 1},{price - 1},{price},100\n"
        result = app.seed_source(
            {
                "content_base64": base64.b64encode(content.encode()).decode(),
                "filename": f"learning-{day}.csv",
                "source_name": fixed["source_name"],
                "spec": fixed,
                **archive,
                "request_id": str(uuid4()),
            },
            wait=True,
        )
        assert result["status"] == "PUBLISHED", result
        snapshots.append(result["snapshot_id"])
    return snapshots
