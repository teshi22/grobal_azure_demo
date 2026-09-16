"""Cosmos DB クライアント — MCP ツール用

travel-requests コンテナへの読み書きを提供する。
"""

from __future__ import annotations

import logging
import os

from azure.cosmos.aio import CosmosClient, ContainerProxy
from azure.identity.aio import DefaultAzureCredential

logger = logging.getLogger(__name__)

_client: CosmosClient | None = None

COSMOS_ENDPOINT = os.environ.get("COSMOS_ENDPOINT", "")
COSMOS_DATABASE = os.environ.get("COSMOS_DATABASE", "travel-agent")
COSMOS_CONTAINER = os.environ.get("COSMOS_TRAVEL_REQUEST_CONTAINER", "travel-requests")
COSMOS_APPROVAL_GRANT_CONTAINER = os.environ.get(
    "COSMOS_APPROVAL_GRANT_CONTAINER",
    "approval-grants",
)
COSMOS_CONVERSATION_CONTAINER = os.environ.get(
    "COSMOS_CONVERSATION_CONTAINER",
    "conversations",
)


def _get_client() -> CosmosClient:
    global _client
    if _client is None:
        if not COSMOS_ENDPOINT:
            raise RuntimeError("COSMOS_ENDPOINT is not set")
        credential = DefaultAzureCredential()
        _client = CosmosClient(COSMOS_ENDPOINT, credential=credential)
    return _client


def get_container() -> ContainerProxy:
    """travel-requests コンテナを返す"""
    client = _get_client()
    db = client.get_database_client(COSMOS_DATABASE)
    return db.get_container_client(COSMOS_CONTAINER)


def get_approval_grant_container() -> ContainerProxy:
    """Return the approval grant container."""
    client = _get_client()
    db = client.get_database_client(COSMOS_DATABASE)
    return db.get_container_client(COSMOS_APPROVAL_GRANT_CONTAINER)


def get_conversation_container() -> ContainerProxy:
    """Return the BFF conversation ownership container."""
    client = _get_client()
    db = client.get_database_client(COSMOS_DATABASE)
    return db.get_container_client(COSMOS_CONVERSATION_CONTAINER)
