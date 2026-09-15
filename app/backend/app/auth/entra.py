"""Entra ID authentication for FastAPI routes."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.config import settings

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)
_jwks_client: PyJWKClient | None = None


def _claim_value(claims: list[dict], *names: str) -> str:
    for claim in claims:
        if claim.get("typ") in names and claim.get("val"):
            return str(claim["val"])
    return ""


def _easy_auth_principal(request: Request) -> dict | None:
    encoded = request.headers.get("x-ms-client-principal")
    if not encoded:
        return None
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        principal = json.loads(base64.b64decode(padded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid client principal") from exc

    claims = principal.get("claims", [])
    subject = _claim_value(
        claims,
        "http://schemas.microsoft.com/identity/claims/objectidentifier",
        "oid",
        "sub",
    )
    if not subject:
        raise HTTPException(status_code=401, detail="Client principal has no subject")
    return {
        "sub": subject,
        "oid": subject,
        "name": _claim_value(claims, "name"),
        "email": _claim_value(
            claims,
            "preferred_username",
            "emails",
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
        ),
        "source": "easy_auth",
    }


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(
            f"https://login.microsoftonline.com/{settings.entra_tenant_id}/discovery/v2.0/keys",
            cache_keys=True,
        )
    return _jwks_client


def _validate_bearer_token(token: str) -> dict:
    signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
    issuer = settings.entra_issuer or (
        f"https://login.microsoftonline.com/{settings.entra_tenant_id}/v2.0"
    )
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=settings.entra_client_id,
        issuer=issuer,
        options={"require": ["exp", "iat", "iss", "aud"]},
    )
    subject = claims.get("oid") or claims.get("sub")
    if not subject:
        raise jwt.InvalidTokenError("Token has no oid or sub claim")
    return {**claims, "sub": str(subject), "source": "bearer"}


async def verify_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> dict:
    """Validate Container Apps Easy Auth or an Entra bearer token."""
    principal = _easy_auth_principal(request)
    if principal:
        return principal

    if not settings.entra_tenant_id or not settings.entra_client_id:
        return {
            "sub": "dev-user",
            "oid": "dev-user",
            "name": "Developer",
            "source": "dev",
        }

    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        return await asyncio.to_thread(
            _validate_bearer_token,
            credentials.credentials,
        )
    except jwt.PyJWTError as exc:
        logger.warning("Entra token validation failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid access token") from exc


CurrentUser = Annotated[dict, Depends(verify_token)]
