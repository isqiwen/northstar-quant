"""Contract pagination and request ownership cannot collapse into request counts."""

from sqlalchemy import text

from northstar_quant.data_management.contract_data.collection_query import search
from northstar_quant.data_management.tushare.job_query import search as jobs
from tests.data_management.test_tushare import automatic, pending  # noqa: F401


def test_contract_query_paginates_beyond_old_limit_and_filters_hierarchy(automatic):  # noqa: F811
    e = automatic._engine
    with e.begin() as c:
        c.execute(
            text("""INSERT INTO data_sync_contracts(ts_code,exchange,product,kind,details)
            SELECT 'CU'||lpad(n::text,4,'0')||'.SHF','SHFE','CU','1',
                jsonb_build_object('name','铜'||n,'list_date','20110101',
                                   'delist_date','20120113','last_ddate','20120120')
            FROM generate_series(1,105) n""")
        )
        c.execute(
            text("""INSERT INTO data_contract_collections(scope,start_date,end_date)
            SELECT ts_code,'2011-01-01'::date,'2012-01-13'::date
            FROM data_sync_contracts WHERE product='CU'""")
        )
    query = dict(
        exchange="SHFE", product="CU", search="", status="COLLECTING", offset=100, limit=20
    )
    result = search(e, **query)
    assert result["total"] == 105
    assert len(result["items"]) == 5
    assert result["items"][0]["scope"] == "CU0101.SHF"
    assert result["items"][0]["display_name"] == "铜101"
    assert result["items"][0]["last_trade_date"] == "2012-01-13"
    assert search(e, **{**query, "search": "铜105", "offset": 0})["total"] == 1
    assert search(e, **{**query, "exchange": "DCE", "offset": 0})["total"] == 0
    assert search(e, **{**query, "status": "PUBLISHED", "offset": 0})["total"] == 0


def test_contract_diagnostics_use_owner_links_including_shared_product_data(automatic):  # noqa: F811
    e = automatic._engine
    request = pending(automatic)
    with e.begin() as c:
        c.execute(text("UPDATE data_sync_jobs SET scope='SHFE:RB',dataset='warehouse'"))
    query = dict(dataset="", status="", offset=0, limit=20)
    result = jobs(e, owner_scope="RB2610.SHF", **query)
    assert result["total"] == 1
    assert result["items"][0]["request_id"] == request
    assert result["items"][0]["scope"] == "SHFE:RB"
    assert jobs(e, owner_scope="RB2611.SHF", **query)["total"] == 0
    assert jobs(e, **query)["total"] == 1


def test_collection_query_uses_authenticated_owned_protocol(automatic):  # noqa: F811
    from northstar_quant.apps.data_hub.application import create_app
    from tests.apps.browser import ProtocolClient, login_response

    with ProtocolClient(
        create_app(automatic._engine, automatic), base_url="http://127.0.0.1"
    ) as client:
        csrf = login_response(client).json()["csrf"]
        client.headers.update({"x-northstar-csrf": csrf, "origin": "http://127.0.0.1"})
        result = client.post(
            "/api/sync/contracts/query",
            json=dict(exchange="", product="", search="", status="", offset=0, limit=20),
        )
        assert result.status_code == 200
        assert result.json()["total"] == 1
        assert result.json()["items"][0]["scope"] == "RB2610.SHF"
