"""Prepare fixed research input from an existing Tushare publication only."""

from typing import Literal
from uuid import UUID

from fastapi import FastAPI, Request
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from northstar_quant.data_management.library import DataLibrary
from northstar_quant.data_management.research_input import ImportSpec
from northstar_quant.data_management.tushare.research import submit
from northstar_quant.web.access import WorkspaceAccess
from northstar_quant.web.datasets import ImportSpecification
from northstar_quant.web.requests import ApiModel

from .processing_api import ProcessingAttempt


class PrepareResearchRequest(ApiModel):
    receipt_id: str
    request_id: str
    specification: ImportSpecification
    label_convention: Literal["BAR_START", "BAR_END"]
    interpretation_reference: str = Field(min_length=1, max_length=512)


def register(app: FastAPI, access: WorkspaceAccess, library: DataLibrary) -> None:
    @app.post(
        "/api/research-inputs",
        response_model=ProcessingAttempt,
        response_model_exclude_unset=True,
        status_code=202,
    )
    async def prepare(request: Request, document: PrepareResearchRequest) -> dict[str, object]:
        access.protect(request)
        return await run_in_threadpool(
            submit,
            library,
            receipt_id=UUID(document.receipt_id),
            request_id=UUID(document.request_id),
            specification=ImportSpec.from_mapping(document.specification.model_dump()),
            label_convention=document.label_convention,
            interpretation_reference=document.interpretation_reference,
        )
