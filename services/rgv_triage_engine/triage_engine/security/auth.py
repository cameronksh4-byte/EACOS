"""Verification of Supabase Auth access tokens for broker-facing endpoints.

Supabase projects sign access tokens either with asymmetric signing keys
(ES256/RS256, public keys published at ``/auth/v1/.well-known/jwks.json``) or,
on older projects, with a shared HS256 secret. Both are supported; the
algorithm allow-list is fixed per mode so an attacker cannot downgrade to
``none`` or to HS256-with-the-public-key.

The verified ``sub`` is the tenant: RLS policies compare ``tenant_id`` with
``auth.uid()``, which is this same claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import anyio
import jwt
from fastapi import HTTPException, Request, status

ASYMMETRIC_ALGORITHMS = ["ES256", "RS256"]
REQUIRED_CLAIMS = ["exp", "iat", "sub", "aud", "iss"]
LEEWAY_SECONDS = 30


@dataclass(frozen=True, slots=True)
class BrokerPrincipal:
    """An authenticated broker user."""

    user_id: UUID
    session_id: str | None
    aal: str | None


class TokenVerificationError(Exception):
    """The bearer token is missing, malformed, expired or not ours."""


class SupabaseJWTVerifier:
    """Validates Supabase access tokens.

    Args:
        issuer: ``https://<project-ref>.supabase.co/auth/v1``.
        audience: Expected ``aud`` claim (``authenticated``).
        jwks_url: JWKS endpoint for asymmetric signing keys.
        hs256_secret: Legacy shared JWT secret (used only if no JWKS URL).
        jwk_client: Injectable ``jwt.PyJWKClient`` (tests).
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str = "authenticated",
        jwks_url: str | None = None,
        hs256_secret: str | None = None,
        jwk_client: Any | None = None,
    ) -> None:
        if not issuer:
            raise ValueError("issuer is required")
        if not (jwks_url or jwk_client or hs256_secret):
            raise ValueError("configure a JWKS URL or the legacy HS256 secret")
        if hs256_secret is not None and len(hs256_secret) < 32:
            raise ValueError("HS256 secret must be at least 32 characters")
        self._issuer = issuer
        self._audience = audience
        self._hs256_secret = hs256_secret
        self._jwk_client = jwk_client or (
            jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=600, timeout=5)
            if jwks_url
            else None
        )

    async def verify(self, token: str) -> BrokerPrincipal:
        """Verify ``token`` and return the broker identity.

        Raises:
            TokenVerificationError: On any failure.
        """
        try:
            if self._jwk_client is not None:
                signing_key = await anyio.to_thread.run_sync(
                    self._jwk_client.get_signing_key_from_jwt, token
                )
                key: Any = signing_key.key
                algorithms = ASYMMETRIC_ALGORITHMS
            else:
                key = self._hs256_secret
                algorithms = ["HS256"]
            claims = jwt.decode(
                token,
                key,
                algorithms=algorithms,
                audience=self._audience,
                issuer=self._issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": REQUIRED_CLAIMS},
            )
        except jwt.PyJWTError as exc:
            raise TokenVerificationError(type(exc).__name__) from exc

        if claims.get("role") != "authenticated":
            raise TokenVerificationError("role is not authenticated")
        try:
            user_id = UUID(str(claims["sub"]))
        except ValueError as exc:
            raise TokenVerificationError("sub is not a UUID") from exc
        return BrokerPrincipal(
            user_id=user_id,
            session_id=claims.get("session_id"),
            aal=claims.get("aal"),
        )


async def require_broker(request: Request) -> BrokerPrincipal:
    """FastAPI dependency: authenticate the caller as a broker user."""
    verifier: SupabaseJWTVerifier | None = request.app.state.services.jwt_verifier
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if verifier is None or scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return await verifier.verify(token.strip())
    except TokenVerificationError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
