"""同步字段变更单端点：元数据同步 → 数据目录的字段差量协同链路。

- 值班员向同步作业提交源端目录字段差量文件，落库为同步字段变更单；
- 数据目录在同步作业的回执内领取变更单，按字段应用确认过的差量；
- 读湖表结构以目录生效类型为准，每列可追溯到来源变更单；
- 目录列可被值班员独立调整，与导入差量冲突时保留目标端并列入待核对。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.data_catalog import DataCatalogService
from app.services.metadata_sync import MetadataSyncService

sync_router = APIRouter(prefix="/api/metadata-sync", tags=["同步字段变更单"])
catalog_router = APIRouter(prefix="/api/data-catalog", tags=["目录字段结构"])


class FieldDiff(BaseModel):
    field: str
    change: str
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None

    model_config = {"populate_by_name": True}


class SyncBatchPayload(BaseModel):
    """目录字段差量文件：源端版本、文件基线版本（下载时的目标端版本）与字段差量。"""

    table: str
    source_version: str
    base_version: int
    diffs: list[FieldDiff]


class ColumnTypePayload(BaseModel):
    type: str


def _ticket_or_404(ticket_id: int) -> dict[str, Any]:
    ticket = MetadataSyncService().get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"同步字段变更单 {ticket_id} 不存在")
    return ticket


@sync_router.post("/{job_id}/batches")
def submit_sync_batch(job_id: int, payload: SyncBatchPayload) -> dict[str, Any]:
    """提交同步批次：上传目录字段差量文件，落库为变更单（同文件幂等）。"""
    service = MetadataSyncService()
    ticket, message, duplicated = service.submit_batch(
        job_id, payload.model_dump(by_alias=True)
    )
    if ticket is None:
        raise HTTPException(status_code=400, detail=message)
    return {
        "ok": True,
        "duplicated": duplicated,
        "message": message,
        "ticket": service._ticket_view(ticket),
    }


@sync_router.get("/{job_id}/receipt")
def sync_job_receipt(job_id: int) -> dict[str, Any]:
    """同步作业应用回执：变更单、已应用字段、待核对范围与双端版本。"""
    receipt = MetadataSyncService().sync_receipt(job_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail=f"同步任务 {job_id} 不存在或已归档")
    return receipt


@sync_router.get("/tickets/{ticket_id}")
def get_sync_ticket(ticket_id: int) -> dict[str, Any]:
    """变更单详情：本次输入版本与目标端版本均可在此核对。"""
    return MetadataSyncService()._ticket_view(_ticket_or_404(ticket_id))


@sync_router.post("/tickets/{ticket_id}/apply")
def apply_sync_ticket(ticket_id: int) -> dict[str, Any]:
    """数据目录领取变更单，按字段应用确认过的差量（重复领取结果不变）。"""
    service = MetadataSyncService()
    ticket, message = service.apply_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail=message)
    return {"ok": True, "message": message, "ticket": service._ticket_view(ticket)}


@catalog_router.get("/tables/{table}/schema")
def read_table_schema(table: str) -> dict[str, Any]:
    """读湖表结构：以目录生效类型为准，每列可追溯来源变更单。"""
    return DataCatalogService().effective_schema(table)


@catalog_router.post("/tables/{table}/columns/{field}")
def update_catalog_column(
    table: str, field: str, payload: ColumnTypePayload
) -> dict[str, Any]:
    """值班员独立调整目录列类型（模拟目标端在文件下载后的并发修改）。"""
    column, message = DataCatalogService().update_column_type(table, field, payload.type)
    if column is None:
        raise HTTPException(status_code=404, detail=message)
    return {"ok": True, "message": message, "column": column}


@catalog_router.get("/pending-review")
def pending_review_scope(table: str | None = Query(default=None)) -> dict[str, Any]:
    """待核对范围：与目标端冲突、尚未人工处理的目录列。"""
    scope = DataCatalogService().pending_review_scope(table)
    return {"total": len(scope), "items": scope}


# main.py 按 module.router 统一注册，这里把两个子路由合并暴露。
router = APIRouter()
router.include_router(sync_router)
router.include_router(catalog_router)
