from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

ops_router = APIRouter(tags=["Operations & Observability"])


@ops_router.get("/health")
def health(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.app_env,
    }


@ops_router.get("/ready")
def readiness(request: Request) -> Response:
    db = request.app.state.db
    try:
        ready, missing_tables = db.check_readiness()
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "database": "unreachable"},
        )

    if not ready:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "not_ready",
                "database": "connected",
                "missing_tables": missing_tables,
            },
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "ready", "database": db.engine.dialect.name},
    )


@ops_router.get("/metrics")
def metrics(request: Request) -> PlainTextResponse:
    registry = request.app.state.metrics
    db = request.app.state.db
    body = registry.render_prometheus(db)
    return PlainTextResponse(content=body, media_type=PROMETHEUS_CONTENT_TYPE)
