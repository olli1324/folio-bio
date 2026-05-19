"""Lightweight Supabase auth integration.

The frontend talks to Supabase Auth directly (signup, login, refresh) and
forwards the resulting access token to *our* backend on every API call via
the standard `Authorization: Bearer <jwt>` header.

This module's only job is to validate that token by asking Supabase
"who is this user?" — `GET /auth/v1/user` with the JWT. If Supabase
returns a user, we accept the claim; if not, the caller is treated as
anonymous. No JWT secret needed on our side, no signature library to keep
patched.

We cache the (token -> user_id) mapping in-process for 60 seconds to avoid
hammering Supabase on every event in an SSE stream.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger("biolitagent.auth")

_CACHE_TTL_SECONDS = 60.0
_HTTP_TIMEOUT = 5.0


@dataclass
class AuthContext:
    """What the backend knows about the caller of a single request.

    `user_id` is None when the caller is anonymous (no token / invalid
    token / Supabase not reachable). `email` is the best-effort label
    for display.
    """
    user_id: str | None = None
    email: str | None = None

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None


_cache: dict[str, tuple[AuthContext, float]] = {}


async def resolve_auth(authorization_header: str | None) -> AuthContext:
    """Turn an `Authorization: Bearer <jwt>` header into an AuthContext.

    Returns an anonymous AuthContext if anything is wrong with the token,
    Supabase is unreachable, or no Supabase URL is configured. Never raises.
    """
    if not authorization_header:
        return AuthContext()

    token = _extract_bearer(authorization_header)
    if not token:
        return AuthContext()

    # Cache hit fast-path.
    now = time.monotonic()
    cached = _cache.get(token)
    if cached is not None and (now - cached[1]) < _CACHE_TTL_SECONDS:
        return cached[0]

    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    apikey = os.getenv("SUPABASE_SECRET_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "")
    if not url or not apikey:
        return AuthContext()

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                f"{url}/auth/v1/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "apikey": apikey,
                },
            )
            if resp.status_code != 200:
                # Invalid / expired token. Treat as anonymous; never 401 the
                # caller -- our app still functions in anonymous mode.
                ctx = AuthContext()
                _cache[token] = (ctx, now)
                return ctx
            data = resp.json()
            ctx = AuthContext(
                user_id=data.get("id"),
                email=data.get("email"),
            )
            _cache[token] = (ctx, now)
            return ctx
    except httpx.HTTPError as exc:
        logger.warning("Supabase /auth/v1/user lookup failed: %s", exc)
        return AuthContext()


def _extract_bearer(header: str) -> str | None:
    parts = header.strip().split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None
