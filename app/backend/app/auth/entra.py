"""Entra ID トークン検証ミドルウェア

FastAPI の Depends として使用し、Bearer トークンを検証する。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)


async def verify_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> dict:
    """Bearer トークンを検証し、ユーザー情報を返す

    TODO: 本番では jose/msal を使用して実際にトークンを検証する
    開発環境ではスキップ可能にする
    """
    if not settings.entra_tenant_id or not settings.entra_client_id:
        # 認証未設定 → 開発モード
        return {"sub": "dev-user", "name": "Developer"}

    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = credentials.credentials

    # TODO: トークン検証ロジック
    # - JWKS エンドポイントから公開鍵を取得
    # - トークンの署名、有効期限、audience、issuer を検証
    # - ユーザー情報 (sub, name, email) を抽出
    raise HTTPException(status_code=501, detail="Token verification not implemented")


CurrentUser = Annotated[dict, Depends(verify_token)]
