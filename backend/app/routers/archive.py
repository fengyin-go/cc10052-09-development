"""归档复算专用路由：复算包打包导出/完整性回查、证据核查、切片恢复、清理与审计。

与通用资源路由分开，承载归档库与切片分析器之间的证据网关业务；路由层不做业务
判断，全部规则落在 ArchiveRecoveryService。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.archive_recovery import archive_recovery_service as service

router = APIRouter(prefix="/api/archive", tags=["归档复算"])


class DownloadPayload(BaseModel):
    archive_no: str = Field(alias="归档号")
    revision: str = Field(alias="归档版次")
    operator: str = Field(default="", alias="操作人")
    evidence_id: int | None = Field(default=None, alias="证据id")

    model_config = {"populate_by_name": True}


class RecoveryPayload(BaseModel):
    archive_no: str = Field(alias="归档号")
    operator: str = Field(default="", alias="操作人")
    download_no: str | None = Field(default=None, alias="下载单号")

    model_config = {"populate_by_name": True}


class ArchiveNoPayload(BaseModel):
    archive_no: str = Field(alias="归档号")

    model_config = {"populate_by_name": True}


@router.get("/evidence")
def list_evidence(archive_no: str | None = None) -> dict[str, Any]:
    return {"items": service.list_evidence(archive_no)}


@router.get("/downloads")
def list_downloads(archive_no: str | None = None) -> dict[str, Any]:
    return {"items": service.list_downloads(archive_no)}


@router.get("/downloads/{download_no}")
def get_download_result(download_no: str) -> dict[str, Any]:
    return service.get_download_result(download_no)


@router.post("/downloads")
def create_download(payload: DownloadPayload) -> dict[str, Any]:
    return service.create_download(
        payload.archive_no, payload.revision,
        operator=payload.operator, evidence_id=payload.evidence_id,
    )


@router.post("/downloads/{download_no}/recheck")
def recheck_download(download_no: str) -> dict[str, Any]:
    return service.recheck_download(download_no)


@router.get("/recoveries")
def list_recoveries(archive_no: str | None = None) -> dict[str, Any]:
    return {"items": service.list_recoveries(archive_no)}


@router.post("/recoveries/check")
def check_recovery_evidence(payload: ArchiveNoPayload) -> dict[str, Any]:
    return service.check_recovery_evidence(payload.archive_no)


@router.post("/recoveries")
def start_recovery(payload: RecoveryPayload) -> dict[str, Any]:
    return service.start_recovery(
        payload.archive_no, operator=payload.operator, download_no=payload.download_no)


@router.post("/recoveries/{recovery_no}/complete")
def complete_recovery(recovery_no: str) -> dict[str, Any]:
    return service.complete_recovery(recovery_no)


@router.post("/purge-temp")
def purge_temp() -> dict[str, Any]:
    return service.purge_temp_archives()


@router.get("/audit-logs")
def list_audit_logs(archive_no: str | None = None) -> dict[str, Any]:
    return {"items": service.list_audit(archive_no)}
