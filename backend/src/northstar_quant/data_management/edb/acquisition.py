"""Fetch one completed SHFE contract session without credentials or paid history."""

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from time import monotonic
from urllib.parse import urlencode
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx2 as httpx

from ..library import DataLibrary
from ..research import ImportSpec

ENDPOINT = "https://edb.shinnytech.com/md/kline"


def request_parameters(spec: ImportSpec) -> dict[str, str]:
    if (
        spec.exchange != "SHFE"
        or re.fullmatch(r"[A-Z]{1,3}[0-9]{4}", spec.symbol) is None
        or spec.symbol[:-4] != spec.product
        or not 1 <= int(spec.symbol[-2:]) <= 12
        or spec.timezone != "Asia/Shanghai"
        or spec.currency != "CNY"
        or spec.source_name != "SHINNY_EDB"
        or spec.availability_basis != "FINAL_REVISED"
    ):
        raise ValueError("EDB requires a real SHFE contract, SHINNY_EDB and FINAL_REVISED timing")
    local = ZoneInfo("Asia/Shanghai")
    start, end = spec.session_open.astimezone(local), spec.session_close.astimezone(local)
    if not (9 <= start.hour < 15 and end.hour <= 15) or end - start > timedelta(hours=2):
        raise ValueError("EDB currently accepts one continuous day segment of at most two hours")
    return {
        "symbol": f"SHFE.{spec.symbol.lower()}",
        "period": "60",
        "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": end.strftime("%Y-%m-%d %H:%M:%S"),
    }


def collect(
    library: DataLibrary,
    spec: ImportSpec,
    *,
    request_id: UUID,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, object]:
    """Download once, retain exact response, then queue independent processing.

    A network failure precedes admission: no durable acquisition plan is claimed.
    No redirect, token, automatic retry or unbounded history download is allowed.
    """
    params = request_parameters(spec)
    now = datetime.now(UTC)
    # Conservative subset of the documented free rolling year, avoiding boundary ambiguity.
    if spec.session_open < now - timedelta(days=364) or spec.session_close > now:
        raise ValueError("EDB free collection requires a completed range within the last 364 days")
    spec = replace(
        spec,
        source_reference=f"{ENDPOINT}?{urlencode(params)}",
        availability_note=(
            "Final revised EDB history; available_at = bar start + one minute is a simulated "
            "information clock, not evidence of historical first publication."
        ),
    )
    try:
        with httpx.Client(
            timeout=10,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            headers={"Accept-Encoding": "identity"},
        ) as client:
            deadline = monotonic() + 30
            with client.stream("GET", ENDPOINT, params=params) as response:
                if response.status_code != 200:
                    raise ValueError(
                        f"EDB returned HTTP {response.status_code}; no automatic retry"
                    )
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ValueError("EDB returned unsupported compressed content")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 5242880 or monotonic() > deadline:
                        raise ValueError("EDB response exceeded byte or elapsed-time limit")
                    chunks.append(chunk)
                content = b"".join(chunks)
    except httpx.HTTPError as error:
        raise ValueError("EDB download failed; no processing task was admitted") from error
    return library.submit(
        content,
        filename=f"edb-{spec.symbol}-{spec.trading_day}.csv",
        source_name=spec.source_name,
        use_basis="EDB free minute history; personal research and private retention only.",
        allow_retention=True,
        allow_download=False,
        input_kind="EDB_CSV",
        spec=spec.to_mapping(),
        request_id=str(request_id),
    )
