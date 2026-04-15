"""Foundry Agent Service クライアント

AIProjectClient + OpenAI クライアントを初期化し、
ワークフローノードから Foundry Agent を呼び出すためのユーティリティを提供する。
"""

from __future__ import annotations

import logging

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

from app.config import settings

logger = logging.getLogger(__name__)

_project_client: AIProjectClient | None = None
_openai_client = None


def get_project_client() -> AIProjectClient:
    global _project_client
    if _project_client is None:
        credential = DefaultAzureCredential()
        _project_client = AIProjectClient(
            endpoint=settings.azure_ai_project_endpoint,
            credential=credential,
        )
        logger.info("AIProjectClient initialized")
    return _project_client


def get_openai_client():
    """Foundry Agent 呼び出し用の OpenAI クライアントを取得する"""
    global _openai_client
    if _openai_client is None:
        _openai_client = get_project_client().get_openai_client()
        logger.info("OpenAI client initialized")
    return _openai_client


def get_token_provider():
    """Cognitive Services 用トークンプロバイダーを取得する"""
    credential = DefaultAzureCredential()
    return get_bearer_token_provider(
        credential, "https://cognitiveservices.azure.com/.default"
    )
