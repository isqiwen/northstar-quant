"""Data Hub entry uses library controls; admission still belongs to DataLibrary."""

import json
from uuid import uuid4

from fastapi import FastAPI, Request
from nicegui import events, ui
from starlette.concurrency import run_in_threadpool

from northstar_quant.data_management.library import DataLibrary
from northstar_quant.web.components import page_header, record


def register(app: FastAPI, library: DataLibrary) -> None:
    @ui.page("/", api_router=app.router)  # type: ignore[arg-type]
    async def home(request: Request) -> None:
        authorize = page_header(request)
        ui.label("已发布数据")
        datasets = await run_in_threadpool(library.list_datasets)
        for dataset in datasets:
            value = dataset.to_dict()
            ui.link(
                f"{value['symbol']} · {value['trading_day']} · {value['bar_count']} bars",
                f"/datasets/{value['snapshot_id']}",
            )
        ui.label("上传 CSV 原文（最多 5 MiB）")
        ui.label("参数填写当前导入说明中的 source 字段，不包含 file；来源、时段与许可必须真实。")
        source = ui.input("来源名称")
        basis = ui.textarea("用途与留存依据")
        retention = ui.checkbox("确认有权留存并用于研究和备份", value=False)
        download = ui.checkbox("允许本机下载", value=False)
        spec = ui.textarea("处理参数 JSON", value="{}")
        outcome = ui.column()

        async def receive(event: events.UploadEventArguments) -> None:
            authorize()
            content = await event.file.read()
            authorize()
            try:
                parameters = json.loads(spec.value)
                if not isinstance(parameters, dict):
                    raise ValueError("处理参数必须为对象")
                result = await run_in_threadpool(
                    library.receive,
                    content,
                    filename=event.file.name,
                    source_name=source.value,
                    use_basis=basis.value,
                    allow_retention=retention.value,
                    allow_download=download.value,
                    input_kind="RECEIVED_CSV",
                    upstream_source_id=None,
                    transformation_note=None,
                    spec=parameters,
                    request_id=str(uuid4()),
                )
                with outcome:
                    record(result)
            except (ValueError, PermissionError) as error:
                ui.notify(str(error)[:500], type="negative")

        ui.upload(on_upload=receive, max_file_size=5 * 1024 * 1024, auto_upload=False).props(
            "accept=.csv max-files=1"
        )
