from __future__ import annotations

import base64
import binascii
from typing import cast
from urllib.parse import quote
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from starlette.concurrency import run_in_threadpool

from northstar_quant.data.library import DataLibrary
from northstar_quant.web.data_views import (
    _attempt_page,
    _dataset_page,
    _lineage_panel,
    _source_page,
    _sources_workspace,
)
from northstar_quant.web.html import workspace_page
from northstar_quant.web.requests import _object, _read_object, _string_field, _uuid_field
from northstar_quant.web_access import (
    WorkspaceAccess,
)


def register(app: FastAPI, access: WorkspaceAccess, library: DataLibrary) -> None:
    @app.get("/api/datasets")
    def accepted_datasets(limit: int = 50) -> list[dict[str, object]]:
        return [item.to_dict() for item in library.list_datasets(limit=limit)]

    @app.get("/api/datasets/{snapshot_id}")
    def dataset_details(snapshot_id: UUID) -> dict[str, object]:
        return library.describe_dataset(snapshot_id).to_dict()

    @app.get("/api/datasets/{snapshot_id}/lineage")
    def dataset_lineage(snapshot_id: UUID) -> dict[str, object]:
        return library.lineage(snapshot_id)

    @app.get("/datasets/{snapshot_id}", response_class=HTMLResponse)
    async def show_dataset(request: Request, snapshot_id: UUID) -> HTMLResponse:
        data = await run_in_threadpool(library.describe_dataset, snapshot_id)
        lineage = await run_in_threadpool(library.lineage, snapshot_id)
        return workspace_page(
            access, request, "数据详情", _dataset_page(data.to_dict()) + _lineage_panel(lineage)
        )

    @app.post("/api/import")
    async def upload(request: Request) -> dict[str, object]:
        access.protect(request)
        payload = await _read_object(request)
        if set(payload) != {
            "content_base64",
            "filename",
            "source_name",
            "use_basis",
            "allow_retention",
            "allow_download",
            "input_kind",
            "upstream_source_id",
            "transformation_note",
            "spec",
            "request_id",
        }:
            raise ValueError(
                "上传需要原文字节、来源与权限声明、处理参数和命令身份，不能提供本地路径。"
            )
        encoded = _string_field(payload, "content_base64")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("content_base64 必须是严格 Base64 编码的原始文件字节。") from error
        for name in ("allow_retention", "allow_download"):
            if type(payload[name]) is not bool:
                raise ValueError(f"{name} 必须是明确的布尔值。")
        upstream = payload["upstream_source_id"]
        note = payload["transformation_note"]
        if note is not None and not isinstance(note, str):
            raise ValueError("transformation_note 必须是文本或 null。")
        return await run_in_threadpool(
            library.receive,
            content,
            filename=_string_field(payload, "filename"),
            source_name=_string_field(payload, "source_name"),
            use_basis=_string_field(payload, "use_basis"),
            allow_retention=cast(bool, payload["allow_retention"]),
            allow_download=cast(bool, payload["allow_download"]),
            input_kind=_string_field(payload, "input_kind"),
            upstream_source_id=None
            if upstream is None
            else _uuid_field(payload, "upstream_source_id"),
            transformation_note=note,
            spec=_object(payload["spec"]),
            request_id=str(_uuid_field(payload, "request_id")),
        )

    @app.get("/sources", response_class=HTMLResponse)
    async def sources_home(request: Request) -> HTMLResponse:
        def content() -> str:
            return _sources_workspace(
                library.list_sources(), library.list_attempts(), library.list_rejections()
            )

        return workspace_page(
            access, request, "来源与处理", await run_in_threadpool(content), mode="本机来源归档"
        )

    @app.get("/sources/{source_id}", response_class=HTMLResponse)
    async def source_page(request: Request, source_id: UUID) -> HTMLResponse:
        source = await run_in_threadpool(library.source, source_id)
        return workspace_page(
            access, request, "来源详情", _source_page(source), mode="本机来源归档"
        )

    @app.get("/attempts/{attempt_id}", response_class=HTMLResponse)
    async def attempt_page(request: Request, attempt_id: UUID) -> HTMLResponse:
        attempt = await run_in_threadpool(library.attempt, attempt_id)
        return workspace_page(
            access, request, "处理尝试", _attempt_page(attempt), mode="本机来源处理"
        )

    @app.get("/api/sources")
    def list_sources(limit: int = 50) -> list[dict[str, object]]:
        return library.list_sources(limit=limit)

    @app.get("/api/sources/{source_id}")
    def source_details(source_id: UUID) -> dict[str, object]:
        return library.source(source_id)

    @app.get("/api/attempts")
    def list_attempts(limit: int = 50) -> list[dict[str, object]]:
        return library.list_attempts(limit=limit)

    @app.get("/api/attempts/{attempt_id}")
    def attempt_details(attempt_id: UUID) -> dict[str, object]:
        return library.attempt(attempt_id)

    @app.get("/api/sources/{source_id}/download")
    async def download_source(request: Request, source_id: UUID) -> Response:
        access.require_request(request)
        filename, content = await run_in_threadpool(library.download, source_id)
        return Response(
            content,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
            },
        )

    @app.post("/api/sources/{source_id}/reprocess")
    async def reprocess_source(request: Request, source_id: UUID) -> dict[str, object]:
        access.protect(request)
        payload = await _read_object(request)
        if set(payload) != {"spec", "request_id"}:
            raise ValueError("重处理只接受 spec 和 request_id，不能更换原文或来源权限。")
        return await run_in_threadpool(
            library.reprocess,
            source_id,
            spec=_object(payload["spec"]),
            request_id=str(_uuid_field(payload, "request_id")),
        )
