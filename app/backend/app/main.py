"""出張申請エージェント — FastAPI バックエンド + 静的ファイル配信"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routers import conversations, stream

logger = logging.getLogger(__name__)

STATIC_DIR = Path("/app/static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """アプリケーション起動/終了時の処理"""
    logger.info("Starting travel-agent backend...")
    from app.services.cosmos import get_cosmos_client

    app.state.cosmos_client = get_cosmos_client()
    logger.info("Cosmos DB client initialized")
    yield
    if hasattr(app.state, "cosmos_client") and app.state.cosmos_client:
        await app.state.cosmos_client.close()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Travel Request Agent API",
    description="AI 出張申請エージェント — Agent Framework + Foundry Agent",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs" if settings.enable_docs else None,
    redoc_url="/api/redoc" if settings.enable_docs else None,
    openapi_url="/api/openapi.json" if settings.enable_docs else None,
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
app.include_router(stream.router, prefix="/api")


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
