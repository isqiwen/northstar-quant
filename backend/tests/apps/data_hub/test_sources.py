"""Source queries keep original evidence and enforce download permissions."""

from northstar_quant.apps.data_hub import create_app
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from tests.apps.browser import ProtocolClient, _browser_session, _seed_source, _upload_request
from tests.data_management.test_library import _study


def test_private_source_query_never_grants_download(postgres_engine, clean_database, tmp_path):
    content, specification, _ = _study()
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "sources"))
    payload = _upload_request(content, specification)
    payload["allow_download"] = False
    attempt = _seed_source(library, payload)
    with ProtocolClient(
        create_app(postgres_engine, library), base_url="http://127.0.0.1"
    ) as client:
        _browser_session(client)
        source = client.get(f"/api/sources/{attempt['source_id']}").json()
        assert source["byte_count"] == len(content)
        assert client.get(f"/api/sources/{attempt['source_id']}/download").status_code == 403
        assert client.post("/api/import", json=payload).status_code == 404
