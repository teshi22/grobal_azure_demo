"""出張申請一覧 API エンドポイント

GET /api/travel-requests          — 申請一覧取得
GET /api/travel-requests/{id}     — 申請詳細取得
"""

import logging

from fastapi import APIRouter, HTTPException

from app.services.cosmos import get_travel_request_store

router = APIRouter(tags=["travel-requests"])
logger = logging.getLogger(__name__)


@router.get("/travel-requests")
async def list_travel_requests(limit: int = 50):
    """出張申請の一覧を取得する"""
    store = get_travel_request_store()
    items = await store.list_all(limit=limit)
    # Cosmos DB メタデータを除去
    return [_clean_doc(item) for item in items]


@router.get("/travel-requests/{request_id}")
async def get_travel_request(request_id: str):
    """出張申請の詳細を取得する"""
    store = get_travel_request_store()
    item = await store.get(request_id)
    if not item:
        raise HTTPException(status_code=404, detail="Travel request not found")
    return _clean_doc(item)


def _clean_doc(doc: dict) -> dict:
    """Cosmos DB メタデータフィールドを除去する"""
    return {k: v for k, v in doc.items() if not k.startswith("_")}
