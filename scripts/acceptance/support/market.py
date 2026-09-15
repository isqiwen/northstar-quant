"""Create synthetic provider evidence through the installed downloader, never an external source."""

import json
import subprocess
from pathlib import Path

from support.processes import InstalledApplication

# Synthetic already-admitted facts exercise immutable package reading in the
# installed browser. They are not evidence of Tushare lifecycle completeness.
_PACKAGE_FIXTURE = """
def pin_reader_fixture():
    from northstar_quant.data_management.contract_data.snapshots import write_snapshot
    from northstar_quant.data_management.publications import PublishedDatasets
    from northstar_quant.data_management.tushare.store import serial
    with engine.begin() as c:
        inputs=[serial(r) for r in c.execute(text("SELECT r.*,j.dataset,j.scope "
            "FROM data_sync_jobs j JOIN data_sync_receipts r ON r.receipt_id=j.receipt_id "
            "WHERE j.status='VALIDATED' AND j.scope='RB2610.SHF' "
            "ORDER BY j.dataset,r.receipt_id")).mappings()]
        manifest=dict(rule='SYNTHETIC_BROWSER_ACCEPTANCE',scope='RB2610.SHF',
            exchange='SHFE',product='RB',inputs=inputs)
        artifact=write_snapshot(PublishedDatasets.from_environment().root,manifest,library._files)
        c.execute(text("INSERT INTO data_contract_publications "
            "(publication_id,scope,manifest,manifest_hash,manifest_bytes,path) "
            "VALUES(:id,'RB2610.SHF',CAST(:manifest AS jsonb),:hash,:bytes,:path) "
            "ON CONFLICT DO NOTHING"),dict(id=artifact['publication_id'],
                manifest=json.dumps(artifact["manifest"]),
                hash=artifact['sha256'],bytes=artifact['bytes'],path=artifact['path']))
"""


def seed_settlement(app: InstalledApplication) -> None:
    """Publish exact/null fee columns and a revision through the installed worker."""
    code = """
import json
from sqlalchemy import text
from northstar_quant.apps.storage import open_database
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.data_management.tushare import acquisition,jobs,planning
from northstar_quant.data_management.tushare.catalog import BY_KEY
engine=open_database()
library=DataLibrary(engine,SourceFiles.from_environment())
with engine.begin() as c:
    c.execute(text("UPDATE data_sync_contracts SET details=details||CAST(:names AS jsonb) "
        "WHERE ts_code='RB2610.SHF'"), {'names':json.dumps({'name':'螺纹钢2610'})})
    c.execute(text("INSERT INTO data_sync_calendar VALUES ('SHFE','2026-09-04',true) "
                   "ON CONFLICT DO NOTHING"))
    c.execute(text("UPDATE data_sync_jobs SET next_at=now()+interval '365 days' "
                   "WHERE status IN ('PENDING','WAITING')"))
    planning.enqueue(c,'settlement','RB2610.SHF',
        {'ts_code':'RB2610.SHF','start_date':'20260901','end_date':'20260904'},
        '2026-09-01','2026-09-04')
    c.execute(text("UPDATE data_sync_settings SET api_next_at='{}',next_request_at=now()"))
fields=BY_KEY['settlement'].fields
rows=[]
for day,price in ((1,'3100.125'),(3,None),(4,'3102.375')):
    row=dict.fromkeys(fields)
    row.update(ts_code='RB2610.SHF',trade_date=f'202609{day:02}',exchange='SHFE',
               settle=price,trading_fee_rate='0.050')
    rows.append(row)
for revised in (False, True):
    if revised:
        rows[0]['trading_fee_rate']='0.060'
        with engine.begin() as c:
            c.execute(text("UPDATE data_sync_jobs SET status='PENDING',next_at=now() "
                           "WHERE request_id=:id"), {'id':result['request_id']})
            c.execute(text("UPDATE data_sync_settings SET api_next_at='{}',next_request_at=now()"))
    content=json.dumps({'code':0,'data':{'fields':fields,
        'items':[[row[name] for name in fields] for row in rows]}}).encode()
    acquisition.fetch=lambda *args:content
    result=jobs.process_next(library)
    assert result['status']=='VALIDATED',result
"""
    code = code.replace(
        "with engine.begin() as c:", _PACKAGE_FIXTURE + "\nwith engine.begin() as c:", 1
    )
    code = code.replace(
        "    assert result['status']=='VALIDATED',result",
        "    assert result['status']=='VALIDATED',result\n    pin_reader_fixture()",
    )
    code = code.replace(
        "\nassert result['status']=='VALIDATED',result",
        "\nassert result['status']=='VALIDATED',result\npin_reader_fixture()",
    )
    code = code.replace(
        "assert result['status']=='VALIDATED', result",
        "assert result['status']=='VALIDATED', result\npin_reader_fixture()",
    )
    code = code.replace(
        "result['baseline_receipt_id']=baseline_receipt_id",
        "pin_reader_fixture()\nresult['baseline_receipt_id']=baseline_receipt_id",
    )
    result = subprocess.run(
        [str(Path(app.executable).parent / "python"), "-c", code],
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
        env=app.environment,
        cwd=app.directory,
    )
    if result.returncode:
        raise RuntimeError("Synthetic settlement setup failed: " + result.stderr)


def seed_market(app: InstalledApplication, *, compact: bool = False) -> dict:
    code = """
import json
from datetime import datetime,timedelta
from sqlalchemy import text
from northstar_quant.apps.storage import open_database
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.data_management.tushare import acquisition,jobs,planning
engine=open_database()
library=DataLibrary(engine,SourceFiles.from_environment())
with engine.begin() as c:
    c.execute(text("UPDATE data_sync_jobs SET next_at=now()+interval '365 days' "
        "WHERE status IN ('PENDING','WAITING')"))
    c.execute(text("UPDATE data_sync_settings SET selected_products=ARRAY['SHFE:RB'], "
        "enabled=true,revision=1,"
        "refresh_at=now()+interval '1 day',api_next_at='{}',next_request_at=now()"))
    c.execute(text("INSERT INTO data_sync_contracts(ts_code,exchange,product,"
        "kind,details,planned_revision) "
        "VALUES ('RB2610.SHF','SHFE','RB','1','{}',1) ON CONFLICT DO NOTHING"))
    c.execute(text("UPDATE data_sync_contracts SET details=CAST(:details AS jsonb) "
        "WHERE ts_code='RB2610.SHF'"), {'details':json.dumps(dict(
            list_date='20260901',delist_date='20260904',last_ddate='20260905'))})
    c.execute(text("INSERT INTO data_contract_collections(scope,start_date,end_date) "
        "VALUES('RB2610.SHF','2026-09-01','2026-09-04') ON CONFLICT DO NOTHING"))
    c.execute(text("INSERT INTO data_sync_calendar VALUES ('SHFE','2026-09-01',true),"
        "('SHFE','2026-09-02',false),('SHFE','2026-09-03',true) ON CONFLICT DO NOTHING"))
    planning.enqueue(c,'1min','RB2610.SHF',{'ts_code':'RB2610.SHF'},'2026-09-01','2026-09-03')
    # A rerun retains previous receipts, but explicitly schedules this synthetic request again.
    c.execute(text("UPDATE data_sync_jobs SET status='PENDING',next_at=now(),attempts=0 "
        "WHERE dataset='1min' AND scope='RB2610.SHF' "
        "AND parameters=CAST(:parameters AS jsonb)"),
        {'parameters':json.dumps({'ts_code':'RB2610.SHF'})})
items=[]
for day in (1,3):
    for i in range(220):
        stamp=(datetime(2026,9,day,9)+timedelta(minutes=i)).strftime('%Y-%m-%d %H:%M:%S')
        price=3100+i%20
        items.append(['RB2610.SHF',stamp,str(price),str(price+2),str(price-1),
                      str(price+1),str(i+1),None if i%3==0 else str(5000+i)])
content=json.dumps({'code':0,'data':{'fields':['ts_code','trade_time','open','high','low','close','vol','oi'],'items':items}}).encode()
acquisition.fetch=lambda *args:content
result=jobs.process_next(library)
assert result['status']=='VALIDATED', result
baseline_receipt_id=result["receipt_id"]
# Retain a partial response and a corrected revision for the real browsing controls.
for invalid in (True,False):
    document=json.loads(content)
    if invalid:
        document['data']['items'][0][6]='-1'
    else:
        document['data']['items'][0][2]='3100.5'
    acquisition.fetch=lambda *args:json.dumps(document).encode()
    with engine.begin() as c:
        c.execute(text("UPDATE data_sync_jobs SET status='PENDING',next_at=now() "
            "WHERE request_id=:id"), {'id':result['request_id']})
        c.execute(text("UPDATE data_sync_settings SET api_next_at='{}',next_request_at=now()"))
    result=jobs.process_next(library)
    assert result['status']==('BLOCKED' if invalid else 'VALIDATED'),result
result['baseline_receipt_id']=baseline_receipt_id
print(json.dumps(result))
"""
    if compact:
        code = code.replace(
            "print(json.dumps(result))",
            """
parameters={'ts_code':'RB2610.SHF','start_date':'2026-09-03 00:00:00',
            'end_date':'2026-09-03 23:59:59'}
with engine.begin() as c:
    planning.enqueue(c,'1min','RB2610.SHF',parameters,'2026-09-03','2026-09-03')
    c.execute(text("UPDATE data_sync_settings SET api_next_at='{}',next_request_at=now()"))
document['data']['items']=[row for row in document['data']['items']
                         if row[1].startswith('2026-09-03')]
acquisition.fetch=lambda *args:json.dumps(document).encode()
result=jobs.process_next(library)
assert result['status']=='VALIDATED',result
print(json.dumps(result))
""",
        )
    code = code.replace(
        "with engine.begin() as c:", _PACKAGE_FIXTURE + "\nwith engine.begin() as c:", 1
    )
    code = code.replace(
        "    assert result['status']=='VALIDATED',result",
        "    assert result['status']=='VALIDATED',result\n    pin_reader_fixture()",
    )
    code = code.replace(
        "\nassert result['status']=='VALIDATED',result",
        "\nassert result['status']=='VALIDATED',result\npin_reader_fixture()",
    )
    code = code.replace(
        "assert result['status']=='VALIDATED', result",
        "assert result['status']=='VALIDATED', result\npin_reader_fixture()",
    )
    code = code.replace(
        "result['baseline_receipt_id']=baseline_receipt_id",
        "pin_reader_fixture()\nresult['baseline_receipt_id']=baseline_receipt_id",
    )
    result = subprocess.run(
        [str(Path(app.executable).parent / "python"), "-c", code],
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
        env=app.environment,
        cwd=app.directory,
    )
    if result.returncode:
        raise RuntimeError("Synthetic market setup failed: " + result.stderr)
    return json.loads(result.stdout)
