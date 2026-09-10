"""Spreadsheet upload endpoints — Task 11, Requirements 4.1–4.8."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, File, Path, Query, UploadFile
from fastapi.responses import Response

from app.api.deps import DispatcherOrAdmin, SessionDep
from app.schemas.entities import UploadResult
from app.services.upload_service import upload_service

router = APIRouter(prefix="/upload", tags=["upload"])


@router.post(
    "/orders", response_model=UploadResult, summary="Bulk-import orders from CSV/XLSX"
)
async def upload_orders(
    session: SessionDep,
    user: DispatcherOrAdmin,
    file: Annotated[UploadFile, File(description="CSV or XLSX, <=50 MB and <=10,000 rows")],
) -> UploadResult:
    content = await file.read()
    result = await upload_service.import_file(
        session,
        kind="orders",
        filename=file.filename or "upload.csv",
        content=content,
        acting_user=user.user_id,
    )
    await session.commit()
    return result


@router.post(
    "/vehicles", response_model=UploadResult, summary="Bulk-import vehicles from CSV/XLSX"
)
async def upload_vehicles(
    session: SessionDep,
    user: DispatcherOrAdmin,
    file: Annotated[UploadFile, File(description="CSV or XLSX, <=50 MB and <=10,000 rows")],
) -> UploadResult:
    content = await file.read()
    result = await upload_service.import_file(
        session,
        kind="vehicles",
        filename=file.filename or "upload.csv",
        content=content,
        acting_user=user.user_id,
    )
    await session.commit()
    return result


@router.get(
    "/templates/{kind}",
    summary="Download an upload template (Requirement 4.3)",
    response_class=Response,
)
async def download_template(
    user: DispatcherOrAdmin,
    kind: Annotated[Literal["orders", "vehicles"], Path()],
    fmt: Annotated[Literal["csv", "xlsx"], Query(alias="format")] = "csv",
) -> Response:
    if fmt == "csv":
        return Response(
            content=upload_service.template_csv(kind),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="roe-{kind}-template.csv"'
            },
        )
    return Response(
        content=upload_service.template_xlsx(kind),
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": f'attachment; filename="roe-{kind}-template.xlsx"'},
    )
