"""Create synthetic provider evidence through the installed downloader, never an external source."""

import json
import subprocess
from pathlib import Path

from support.processes import InstalledApplication


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
    c.execute(text("UPDATE data_sync_settings SET enabled=true,revision=1,"
        "refresh_at=now()+interval '1 day',next_request_at=now()"))
    c.execute(text("INSERT INTO data_sync_contracts(ts_code,exchange,product,"
        "kind,details,planned_revision) "
        "VALUES ('RB2610.SHF','SHFE','RB','1','{}',1) ON CONFLICT DO NOTHING"))
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
# Retain a failed response and a corrected revision for the real browsing controls.
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
        c.execute(text("UPDATE data_sync_settings SET next_request_at=now()"))
    result=jobs.process_next(library)
    assert result['status']==('BLOCKED' if invalid else 'VALIDATED'),result
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
    c.execute(text("UPDATE data_sync_settings SET next_request_at=now()"))
document['data']['items']=[row for row in document['data']['items']
                         if row[1].startswith('2026-09-03')]
acquisition.fetch=lambda *args:json.dumps(document).encode()
result=jobs.process_next(library)
assert result['status']=='VALIDATED',result
print(json.dumps(result))
""",
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
