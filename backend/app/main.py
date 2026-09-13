"""FastAPI application factory for the ROE API gateway / BFF."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import api_router
from app.api.v1.ws import router as ws_router
from app.core.config import settings
from app.core.errors import ROEError
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    from app.db.session import dispose_engine, session_scope
    from app.workers.background import background_tasks

    try:
        async with session_scope() as session:
            from app.services.optimisation_service import optimisation_service

            # A run left "in progress" by a crash or restart can never finish;
            # mark it aborted so the console is not stuck behind a phantom run.
            await optimisation_service.abort_all_in_progress(session)
    except Exception as exc:  # pragma: no cover - lets the API boot without a DB
        logger.error("startup_recovery_failed", error=str(exc))

    await background_tasks.start()
    logger.info("roe_api_started", environment=settings.environment)
    try:
        yield
    finally:
        await background_tasks.stop()
        from app.core.events import event_bus

        await event_bus.close()
        await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description=(
            "Route Optimisation Engine — assigns delivery orders to vehicles, plans "
            "capacity-aware routes with delivery time windows, and keeps dispatchers in "
            "control of the plan."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ROEError)
    async def roe_error_handler(request: Request, exc: ROEError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("request_failed", path=request.url.path, error=exc.message)
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Map Pydantic errors to the design's field-level error envelope."""
        fields = []
        for error in exc.errors():
            location = [str(part) for part in error["loc"] if part not in ("body", "query")]
            fields.append(
                {"field": ".".join(location) or "body", "message": error.get("msg", "Invalid")}
            )
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_failed",
                "message": "One or more fields are invalid. Your entries were not saved.",
                "fields": fields,
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "error")
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": code, "message": str(exc.detail)},
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_exception", path=request.url.path, error=str(exc))
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "message": "An unexpected error occurred. The operation was not applied.",
            },
        )

    @app.get("/health", tags=["system"], summary="Liveness probe")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "roe-api", "version": "1.0.0"}

    @app.get("/ready", tags=["system"], summary="Readiness probe")
    async def ready() -> dict[str, str]:
        from sqlalchemy import text

        from app.db.session import session_scope

        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ready"}

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    app.include_router(ws_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
