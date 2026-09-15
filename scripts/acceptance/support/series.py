"""Independent series UI fixtures through the installed supplier-processing path."""

import subprocess
from pathlib import Path

from support.processes import InstalledApplication


def seed_series(app: InstalledApplication) -> None:
    code = """
import json
from sqlalchemy import text
from northstar_quant.apps.storage import open_database
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.data_management.series_data import planning as series,processing
from northstar_quant.data_management.tushare import acquisition,jobs,planning
engine=open_database()
library=DataLibrary(engine,SourceFiles.from_environment())
with engine.begin() as c:
    c.execute(text("UPDATE data_sync_jobs SET next_at=now()+interval '365 days' "
                   "WHERE status IN ('PENDING','WAITING')"))
    c.execute(text("INSERT INTO data_series_collections "
                   "(dataset,scope,exchange,product,name,start_date) "
                   "VALUES('index','ALL','','','南华指数','2026-09-01')"))
for day in ('20260901','20260902'):
    date=f'{day[:4]}-{day[4:6]}-{day[6:]}'
    with engine.begin() as c:
        identity=planning.enqueue(c,'index','ALL',{'start_date':day,'end_date':day},date,date)
        series.link(c,'index','ALL',identity)
        c.execute(text("UPDATE data_sync_settings SET api_next_at='{}',next_request_at=now()"))
    row=dict(ts_code='CU.NH',trade_date=day,open=12,high=13,low=11,close=12,vol=3,amount=2)
    payload=json.dumps(dict(code=0,data=dict(fields=list(row),items=[list(row.values())]))).encode()
    acquisition.fetch=lambda *_:payload
    result=jobs.process_next(library,plan=False)
    assert result['status']=='VALIDATED',result
    result=processing.process_next(engine,library._files)
    assert result['published']==1,result
"""
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
        raise RuntimeError("Synthetic series setup failed: " + result.stderr)
