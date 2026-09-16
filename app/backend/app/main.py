"""出張申請エージェント — FastAPI バックエンド + 静的ファイル配信"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from azure.monitor.opentelemetry import configure_azure_monitor
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routers import conversations, evaluations, stream, travel_requests

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logging.getLogger("app").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)
logging.getLogger("opentelemetry").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
STATIC_DIR = Path(os.environ.get("STATIC_DIR", "/app/static"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """アプリケーション起動/終了時の処理"""
    logger.info("Starting travel-agent backend...")
    from app.services.cosmos import (
        close_cosmos_client,
        get_conversation_store,
        get_cosmos_client,
        get_evaluation_case_store,
        get_evaluation_result_store,
        get_evaluation_run_store,
    )

    from app.services.evaluation_foundry import get_evaluation_service
    get_evaluation_service()
    logger.info("Evaluation service initialized")

    recovery_task = None
    if settings.evaluation_mode == "stub":
        app.state.cosmos_client = None
        from app.services.evaluation_cases import (
            seed_default_evaluation_cases,
        )

        await seed_default_evaluation_cases(
            get_evaluation_case_store(),
            user_id="local-seed",
        )
        logger.info("Local evaluation stub initialized")
    else:
        app.state.cosmos_client = get_cosmos_client()
        logger.info("Cosmos DB client initialized")

        store = get_conversation_store()
        await store.get("__warmup__")
        await get_evaluation_case_store().get("__warmup__", "__warmup__")
        await get_evaluation_run_store().get("__warmup__")
        await get_evaluation_result_store().get("__warmup__", "__warmup__")
        logger.info("Cosmos DB connection pre-warmed")

        from app.services.foundry import get_hosted_responses_client

        get_hosted_responses_client()
        logger.info("Hosted Agent Responses client pre-warmed")

        from app.services.hosted_agent import recover_pending_messages

        recovery_task = asyncio.create_task(
            recover_pending_messages(),
            name="conversation-recovery",
        )
    try:
        yield
    finally:
        if recovery_task is not None:
            recovery_task.cancel()
            with suppress(asyncio.CancelledError):
                await recovery_task
    from app.services.evaluation_foundry import close_evaluation_service
    from app.services.foundry import close_foundry_client

    close_evaluation_service()
    close_foundry_client()
    await close_cosmos_client()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Travel Request Agent API",
    description="AI 出張申請エージェント — Foundry Hosted Agent BFF",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs" if settings.enable_docs else None,
    redoc_url="/api/redoc" if settings.enable_docs else None,
    openapi_url="/api/openapi.json" if settings.enable_docs else None,
)

if settings.applicationinsights_connection_string:
    os.environ["OTEL_TRACES_EXPORTER"] = "none"
    os.environ["OTEL_METRICS_EXPORTER"] = "none"
    configure_azure_monitor(
        connection_string=settings.applicationinsights_connection_string,
        logger_name="app",
        disable_offline_storage=True,
        enable_live_metrics=False,
        enable_performance_counters=False,
        instrumentation_options={
            "azure_sdk": {"enabled": False},
            "fastapi": {"enabled": False},
            "requests": {"enabled": False},
            "urllib": {"enabled": False},
            "urllib3": {"enabled": False},
        },
    )

# CORS: ローカル開発用 (本番では同一オリジンのため不要)
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# API ルート
app.include_router(conversations.router, prefix="/api")
app.include_router(evaluations.router, prefix="/api")
app.include_router(stream.router, prefix="/api")
app.include_router(travel_requests.router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok"}


# --- 静的ファイル配信 (フロントエンド) ---
if STATIC_DIR.exists():
    app.mount("/_next", StaticFiles(directory=STATIC_DIR / "_next"), name="next-assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(request: Request, full_path: str):
        """SPA フォールバック: 静的ファイルがあればそれを返し、なければ index.html"""
        file_path = STATIC_DIR / full_path

        # trailingSlash 対応: /about/ → /about/index.html
        if file_path.is_dir():
            index = file_path / "index.html"
            if index.is_file():
                return FileResponse(index, media_type="text/html")

        if file_path.is_file():
            return FileResponse(file_path)

        # SPA フォールバック → index.html
        index_html = STATIC_DIR / "index.html"
        if index_html.is_file():
            return FileResponse(index_html, media_type="text/html")

        return HTMLResponse("Not Found", status_code=404)
