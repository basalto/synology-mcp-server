"""Async HTTP client for the Synology DSM WebAPI.

DSM authenticates by calling ``SYNO.API.Auth`` ``login`` with an account +
password, which returns a session ID (``sid``). Every subsequent request
carries the sid as the ``_sid`` query parameter.

Key difference from typical REST APIs: DSM reports *both* success and failure
with HTTP 200. Failures are JSON ``{"success": false, "error": {"code": ...}}``.
Error code 119 means "session expired/invalid" — the client transparently
re-authenticates and retries once.

All methods return the parsed ``data`` payload (dict/list). API errors raise
``SynologyError``.
"""

from __future__ import annotations

from typing import Any

import httpx

# Error codes (subset) — 119 is the one the client acts on.
_ERR_SESSION_EXPIRED = 119


class SynologyError(RuntimeError):
    """Raised when the DSM API returns an error or unexpected payload."""


class SynologyClient:
    """Authenticated async client for the Synology DSM WebAPI."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        auth_version: str = "6",
        timeout: float = 15.0,
        verify: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.auth_version = auth_version
        self.timeout = timeout
        self._sid: str | None = None
        self._client = httpx.AsyncClient(timeout=timeout, verify=verify)

    async def __aenter__(self) -> SynologyClient:
        await self.authenticate()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self.logout()
        await self._client.aclose()

    async def logout(self) -> None:
        """Release the current session (best-effort).

        DSM caps concurrent sessions per account; releasing on close prevents
        leaking seats.
        """
        if not self._sid:
            return
        try:
            await self._client.get(
                f"{self.base_url}/webapi/entry.cgi",
                params={
                    "api": "SYNO.API.Auth",
                    "version": self.auth_version,
                    "method": "logout",
                    "session": "Core",
                    "_sid": self._sid,
                },
            )
        except Exception:  # noqa: BLE001 - logout is best-effort
            pass
        finally:
            self._sid = None

    async def authenticate(self) -> str:
        """Log in to DSM and cache the session ID.

        Returns:
            The session ID (sid).
        """
        resp = await self._client.get(
            f"{self.base_url}/webapi/entry.cgi",
            params={
                "api": "SYNO.API.Auth",
                "version": self.auth_version,
                "method": "login",
                "account": self.username,
                "passwd": self.password,
                "session": "Core",
                "format": "sid",
            },
        )
        data = resp.json()
        if not data.get("success"):
            code = (data.get("error") or {}).get("code")
            raise SynologyError(f"DSM login failed (error code {code}): {data}")
        sid = (data.get("data") or {}).get("sid")
        if not sid:
            raise SynologyError(f"DSM login returned no session ID: {data}")
        self._sid = sid
        return sid

    async def _request(
        self,
        api: str,
        method: str,
        *,
        version: str = "1",
        params: dict[str, Any] | None = None,
        _retry: bool = True,
    ) -> Any:
        """Perform an authenticated DSM API call, re-authenticating once on 119.

        DSM returns errors with HTTP 200 and a JSON ``success: false`` body, so
        HTTP status is not a reliable signal; the ``success`` field is.
        """
        if not self._sid:
            await self.authenticate()
        merged: dict[str, Any] = {
            "api": api,
            "version": version,
            "method": method,
            "_sid": self._sid,
        }
        if params:
            merged.update(params)
        resp = await self._client.get(f"{self.base_url}/webapi/entry.cgi", params=merged)
        data = resp.json()
        if not data.get("success"):
            code = (data.get("error") or {}).get("code")
            if code == _ERR_SESSION_EXPIRED and _retry:
                await self.authenticate()
                return await self._request(
                    api, method, version=version, params=params, _retry=False
                )
            raise SynologyError(f"DSM {api}.{method} failed (error code {code}): {data}")
        return data.get("data")

    # ------------------------------------------------------------------
    # System / utilization
    # ------------------------------------------------------------------

    async def system_info(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.System", "info", version="3")

    async def utilization(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.System.Utilization", "get")

    async def dsm_info(self) -> dict[str, Any]:
        return await self._request("SYNO.DSM.Info", "getinfo", version="2")

    # ------------------------------------------------------------------
    # Storage / disks / volumes / pools
    # ------------------------------------------------------------------

    async def storage_load_info(self) -> dict[str, Any]:
        """Disks, volumes, storage pools, SMART status, temperature.

        ``load_info`` is the authoritative single-call storage endpoint — it
        returns the disk list (each with model, size, ``smart_status``, ``temp``,
        ``remain_life``), the volume list, storage pool list, and ports.
        """
        return await self._request("SYNO.Storage.CGI.Storage", "load_info")

    # NOTE: SYNO.Storage.CGI.Volume / .Smart / .Pool exist in the API index but
    # their method names differ per-DSM and return error 103 here. Not needed —
    # load_info above already contains volumes, pools, and per-disk SMART state.

    # ------------------------------------------------------------------
    # Hardware / network / packages
    # ------------------------------------------------------------------

    async def fan_speed(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Hardware.FanSpeed", "get")

    async def network_ethernet(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Network.Ethernet", "list", version="1")

    async def packages(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Package", "list", version="1")
