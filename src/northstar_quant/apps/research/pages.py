"""Native research input controls; durable independent scheduling remains #23."""

import json
from uuid import UUID

from fastapi import FastAPI, Request
from nicegui import ui
from starlette.concurrency import run_in_threadpool

from northstar_quant.data_management.library import DataLibrary
from northstar_quant.research import ResearchConfig, run_research
from northstar_quant.runs import RunStore
from northstar_quant.web.components import page_header


def register(app: FastAPI, library: DataLibrary, store: RunStore) -> None:
    @ui.page("/", api_router=app.router)  # type: ignore[arg-type]
    async def home(request: Request) -> None:
        authorize = page_header(request)
        datasets = await run_in_threadpool(library.list_datasets)
        options = {
            str(item.to_dict()["snapshot_id"]): (
                f"{item.to_dict()['symbol']} · {item.to_dict()['trading_day']}"
            )
            for item in datasets
        }
        selected = ui.select(options, label="固定数据快照")
        parameters = ui.textarea(
            "研究配置 JSON",
            value=json.dumps(ResearchConfig().to_dict(), ensure_ascii=False, indent=2),
        )
        ui.label("当前仅支持有界单日研究；独立持久执行器尚未实现，不用于长任务。")

        async def submit() -> None:
            authorize()

            def execute() -> str:
                dataset = library.load_dataset(identifier)
                result = run_research(dataset, config)
                return store.save(dataset, config, result)

            button.disable()
            try:
                config = ResearchConfig.from_mapping(json.loads(parameters.value))
                identifier = UUID(str(selected.value))
                run_id = await run_in_threadpool(execute)
                ui.navigate.to(f"/runs/{run_id}")
            except (ValueError, LookupError) as error:
                ui.notify(str(error)[:500], type="negative")
            finally:
                button.enable()

        button = ui.button("运行固定研究", on_click=submit)
        ui.label("研究记录")
        for saved in await run_in_threadpool(store.list):
            ui.link(str(saved["run_id"]), f"/runs/{saved['run_id']}")
