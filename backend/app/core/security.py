"""JWT issuance/validation and password hashing."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
import jwt

from app.core.config import settings
from app.core.errors import Unauthenticated

TokenType = Literal["access", "refresh"]

#: bcrypt truncates at 72 bytes; hash a longer password's UTF-8 prefix instead
#: of raising so long passphrases remain usable.
_BCRYPT_MAX_BYTES = 72


def _encode(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


#: bcrypt is deliberately slow. The default cost is right for production, but
#: a suite that creates a user per generated example spends most of its time
#: here, so the test environment uses the minimum work factor.
_BCRYPT_ROUNDS = 4 if settings.environment == "test" else 12


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_encode(password), bcrypt.gensalt(_BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(_encode(password), password_hash.encode("utf-8"))
    except ValueError:  # pragma: no cover - malformed stored hash
        return False


def _signing_key() -> str:
    return settings.jwt_secret_key


def _verification_key() -> str:
    if settings.jwt_algorithm == "RS256" and settings.jwt_public_key:
        return settings.jwt_public_key
    return settings.jwt_secret_key


def create_token(
    *,
    user_id: uuid.UUID,
    email: str,
    role: str,
    token_type: TokenType = "access",
    expires_delta: timedelta | None = None,
) -> str:
    now = datetime.now(UTC)
    if expires_delta is None:
        expires_delta = (
            timedelta(minutes=settings.access_token_ttl_minutes)
            if token_type == "access"
            else timedelta(hours=settings.refresh_token_ttl_hours)
        )
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "type": token_type,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, _signing_key(), algorithm=settings.jwt_algorithm)


def decode_token(token: str, *, expected_type: TokenType | None = "access") -> dict[str, Any]:
    """Validate signature, expiry, issuer, and audience (Requirement 17.4)."""
    try:
        claims = jwt.decode(
            token,
            _verification_key(),
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthenticated("Access token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise Unauthenticated("Missing or invalid credentials") from exc

    if expected_type is not None and claims.get("type") != expected_type:
        raise Unauthenticated(f"Expected a {expected_type} token")
    if "sub" not in claims:
        raise Unauthenticated("Token is missing the subject claim")
    return claims
