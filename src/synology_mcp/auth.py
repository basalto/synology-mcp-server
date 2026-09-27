"""Token authentication for the Synology MCP Server.

Implements a simple bearer-token verifier using FastMCP's auth-provider
interface. When ``MCP_AUTH_TOKEN`` is configured, the HTTP transports
(streamable-http, http, sse) require every request to carry a matching
``Authorization: Bearer *** header.

The token comparison is constant-time (``hmac.compare_digest``) to avoid
timing side-channels.
"""

from __future__ import annotations

import hmac

from fastmcp.server.auth import AccessToken, TokenVerifier


class StaticTokenAuth(TokenVerifier):
    """Verify a single static bearer token configured via environment variable.

    A deliberately minimal auth provider: no OAuth flow, no refresh, no scopes.
    Exactly one token is accepted; anything else is rejected with 401 by the
    FastMCP bearer-auth middleware.

    Args:
        token: The expected bearer token value. Empty/None tokens are treated
            as "no token configured" and must not be passed here (the caller
            should skip registering auth instead).
    """

    def __init__(self, token: str) -> None:
        super().__init__()
        if not token:
            raise ValueError("StaticTokenAuth requires a non-empty token")
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        """Validate a bearer token.

        Args:
            token: The bearer token extracted from the Authorization header.

        Returns:
            An ``AccessToken`` when the token matches, ``None`` otherwise.
        """
        if not token:
            return None
        if not hmac.compare_digest(token, self._token):
            return None
        return AccessToken(
            token=token,
            client_id="synology-mcp-server",
            scopes=[],
        )
