"""Fixed historical request meaning, separate from credentials and HTTP transport."""

import json
import re
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..research import ImportSpec

FIELDS = "ts_code,trade_time,open,close,high,low,vol,amount,oi"
ENDPOINT = "https://api.tushare.pro"


def parameters(spec: ImportSpec) -> dict[str, str]:
    if (
        spec.exchange != "SHFE"
        or re.fullmatch(r"[A-Z]{1,3}[0-9]{4}", spec.symbol) is None
        or spec.symbol[:-4] != spec.product
        or not 1 <= int(spec.symbol[-2:]) <= 12
        or spec.timezone != "Asia/Shanghai"
        or spec.currency != "CNY"
        or spec.source_name != "TUSHARE"
        or spec.availability_basis != "FINAL_REVISED"
    ):
        raise ValueError("Tushare requires a real SHFE contract and FINAL_REVISED research timing")
    local = ZoneInfo("Asia/Shanghai")
    start, end = spec.session_open.astimezone(local), spec.session_close.astimezone(local)
    if not (9 <= start.hour < 15 and end.hour <= 15) or end - start > timedelta(hours=2):
        raise ValueError("current publication accepts a continuous day segment up to two hours")
    # Explicit BAR_END assumption: do not silently treat vendor trade_time as bar start.
    return {
        "ts_code": f"{spec.symbol}.SHF",
        "freq": "1min",
        "start_date": (start + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),
        "end_date": end.strftime("%Y-%m-%d %H:%M:%S"),
    }


def fixed_spec(spec: ImportSpec) -> ImportSpec:
    request = parameters(spec)
    if (
        spec.session_close.astimezone(ZoneInfo("Asia/Shanghai")).date()
        >= datetime.now(ZoneInfo("Asia/Shanghai")).date()
    ):
        raise ValueError("historical sync requires a previous completed local date")
    return replace(
        spec,
        source_reference=json.dumps(
            {"endpoint": ENDPOINT, "api_name": "ft_mins", "params": request}, sort_keys=True
        ),
        availability_note=(
            "Final revised history; trade_time is interpreted as BAR_END in Asia/Shanghai. "
            "Completion is the simulated available_at, not historical first-publication evidence. "
            "Confirm bar labels and coverage against an entitled sample before research use."
        ),
    )
