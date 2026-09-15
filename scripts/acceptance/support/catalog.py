"""Fixed Research material acceptance through installed, authenticated HTTP."""

import json
from time import monotonic, sleep
from uuid import uuid4


def check_catalog(request, url, snapshot_id, configuration, run_id):
    def api(path, body=None):
        return json.loads(request(url + path, body))

    revision = api(
        "/api/factor-revisions",
        {"factor_id": "trend.return", "parameters": {"window_bars": 2}},
    )["revision_id"]
    factor = api(
        "/api/factor-runs",
        {"revision_id": revision, "snapshot_id": snapshot_id, "request_id": str(uuid4())},
    )
    deadline = monotonic() + 30
    while factor["status"] in {"QUEUED", "RUNNING"} and monotonic() < deadline:
        sleep(0.1)
        factor = api("/api/factor-runs/" + factor["attempt_id"])
    assert factor["status"] == "SUCCEEDED"
    api(f"/api/factor-revisions/{revision}/annotations", {"description": "Installed acceptance"})
    version = api(
        "/api/strategy-versions",
        {
            "name": "Installed fixed candidate",
            "configuration_id": configuration["configuration_id"],
            "run_ids": [run_id],
        },
    )["version_id"]
    candidate = api(f"/api/strategy-versions/{version}/publish", {})
    assert candidate["version_id"] == version
    return {"factor": factor, "candidate": candidate}


def check_restored_catalog(application, expected):
    with application.api("research-api") as url:
        factor = json.loads(
            application.request(url + "/api/factor-runs/" + expected["factor"]["attempt_id"])
        )
        assert factor == expected["factor"]
        candidates = json.loads(application.request(url + "/api/strategy-candidates"))
        assert expected["candidate"] in candidates
        revisions = json.loads(application.request(url + "/api/factor-revisions"))
        assert any(
            note["description"] == "Installed acceptance"
            for revision in revisions
            for note in revision["annotations"]
        )
