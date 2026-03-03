import base64
import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import jwt


@dataclass(frozen=True)
class JwtConfig:
    """JWT configuration."""

    secret: str
    algorithm: str
    access_token_ttl_seconds: int


def _get_jwt_config() -> JwtConfig:
    """
    Resolve JWT config from environment.

    Required env vars:
    - JWT_SECRET: secret key for signing tokens
    Optional:
    - JWT_ALGORITHM (default HS256)
    - ACCESS_TOKEN_TTL_SECONDS (default 604800 = 7 days)
    """
    secret = os.getenv("JWT_SECRET")
    if not secret:
        # Avoid insecure defaults; require orchestrator/user to set it.
        raise RuntimeError("JWT_SECRET is not set. Please configure it in the backend environment.")

    alg = os.getenv("JWT_ALGORITHM") or "HS256"
    ttl = int(os.getenv("ACCESS_TOKEN_TTL_SECONDS") or "604800")
    return JwtConfig(secret=secret, algorithm=alg, access_token_ttl_seconds=ttl)


def _pbkdf2_hash(password: str, salt: bytes, iterations: int = 200_000) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


# PUBLIC_INTERFACE
def hash_password(password: str) -> str:
    """Hash a password using PBKDF2-HMAC-SHA256 with a random salt."""
    if not password:
        raise ValueError("Password must not be empty")
    salt = os.urandom(16)
    iterations = 200_000
    dk = _pbkdf2_hash(password, salt, iterations=iterations)
    return "pbkdf2_sha256$%d$%s$%s" % (
        iterations,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )


# PUBLIC_INTERFACE
def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against a PBKDF2 hash string."""
    try:
        scheme, iter_s, salt_b64, dk_b64 = password_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iter_s)
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(dk_b64.encode("ascii"))
        got = _pbkdf2_hash(password, salt, iterations=iterations)
        return hmac.compare_digest(got, expected)
    except Exception:
        return False


# PUBLIC_INTERFACE
def create_access_token(subject: str, extra_claims: Optional[Dict[str, Any]] = None) -> str:
    """Create a signed JWT access token for a given subject (user id)."""
    cfg = _get_jwt_config()
    now = int(time.time())
    payload: Dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + cfg.access_token_ttl_seconds,
    }
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, cfg.secret, algorithm=cfg.algorithm)


# PUBLIC_INTERFACE
def decode_access_token(token: str) -> Dict[str, Any]:
    """Decode/verify a JWT access token and return its claims."""
    cfg = _get_jwt_config()
    return jwt.decode(token, cfg.secret, algorithms=[cfg.algorithm])
