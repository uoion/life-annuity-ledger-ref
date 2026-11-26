import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.ops import ops_router
from app.api.routes import router as v1_router
from app.config import Settings
from app.infra.database import Database
from app.infra.observability import MetricsRegistry, configure_logging, emit_access_log
from app.infra.rate_limiter import SlidingWindowRateLimiter


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings()
    configure_logging(app_settings.log_level)

    db = Database(
        database_url=app_settings.database_url,
        migrations_dir=app_settings.migrations_dir,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.db.run_migrations()
        yield
        application.state.db.dispose()

    application = FastAPI(
        title=app_settings.app_name,
        version=app_settings.app_version,
        description=(
            "Reference cloud-native ZAR Guaranteed Life Annuities backend proving "
            "deterministic financial math, append-only cryptographic audit ledger "
            "integrity, transactional outbox reliability, and POPIA-governed AI explanations."
        ),
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.allowed_origins_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key", "X-Correlation-ID"],
        expose_headers=["X-Correlation-ID", "X-Idempotent-Replay"],
    )

    application.state.settings = app_settings
    application.state.db = db
    application.state.metrics = MetricsRegistry()
    application.state.explain_rate_limiter = SlidingWindowRateLimiter(
        max_requests=app_settings.explain_rate_limit_per_minute,
        window_seconds=60.0,
    )

    @application.middleware("http")
    async def observability_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming_id = request.headers.get("X-Correlation-ID", "").strip()
        correlation_id = incoming_id[:64] if incoming_id else uuid4().hex
        request.state.correlation_id = correlation_id

        started = time.perf_counter()
        response = await call_next(request)
        duration_sec = time.perf_counter() - started

        route_obj = request.scope.get("route")
        route_template = getattr(route_obj, "path", request.url.path)

        response.headers["X-Correlation-ID"] = correlation_id
        application.state.metrics.record_request(
            method=request.method,
            route=route_template,
            status_code=response.status_code,
            duration_seconds=duration_sec,
        )
        emit_access_log(
            correlation_id=correlation_id,
            method=request.method,
            route=route_template,
            status_code=response.status_code,
            duration_ms=duration_sec * 1000.0,
        )
        return response

    application.include_router(ops_router)
    application.include_router(v1_router)
    return application


app = create_app()
