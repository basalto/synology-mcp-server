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

import json
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

    async def _request_sdk(
        self,
        api: str,
        method: str,
        *,
        params: dict[str, Any] | None = None,
        version: str = "1",
        _retry: bool = True,
    ) -> Any:
        """Perform a DSM API call using the modern Synology SDK wire format.

        The DSM 7 web UI's ``synowebapi`` SDK POSTs form-encoded to
        ``/webapi/entry.cgi/{api}`` and JSON-stringifies **every** param value
        (its ``z()`` serializer). This preserves types (e.g. integer TTLs) that
        the old GET query-param style flattens to strings. Required for the
        DNSServer write methods (create/delete) and recommended for anything
        that rejects a param with ``reason: "type"``.
        """
        if not self._sid:
            await self.authenticate()
        body: dict[str, Any] = {
            "api": api,
            "version": version,
            "method": method,
            "_sid": self._sid,
        }
        for key, value in (params or {}).items():
            body[key] = json.dumps(value)
        resp = await self._client.post(
            f"{self.base_url}/webapi/entry.cgi/{api}",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        )
        data = resp.json()
        if not data.get("success"):
            code = (data.get("error") or {}).get("code")
            if code == _ERR_SESSION_EXPIRED and _retry:
                await self.authenticate()
                return await self._request_sdk(
                    api, method, params=params, version=version, _retry=False
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

    # ------------------------------------------------------------------
    # Read-only expansion
    # ------------------------------------------------------------------

    async def dns_zones(self) -> dict[str, Any]:
        """List DNS Server zones (forward + reverse)."""
        return await self._request("SYNO.DNSServer.Zone", "list")

    async def dns_views(self) -> dict[str, Any]:
        return await self._request("SYNO.DNSServer.View", "list")

    async def dns_daemon_status(self) -> dict[str, Any]:
        return await self._request("SYNO.DNSServer.DaemonStatus", "get")

    async def shares(self) -> dict[str, Any]:
        """List shared folders."""
        return await self._request("SYNO.Core.Share", "list")

    async def system_processes(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.System.Process", "list")

    async def logcenter_logs(self) -> dict[str, Any]:
        return await self._request("SYNO.LogCenter.Log", "list", version="2")

    async def network_bonds(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Network.Bond", "list")

    async def hibernation(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Hardware.Hibernation", "get")

    # ------------------------------------------------------------------
    # DNS zone records (read + write)
    # ------------------------------------------------------------------

    async def dns_records(self, zone_name: str) -> dict[str, Any]:
        """List all records in a DNS zone.

        ``zone_name`` is the zone's ``zone_name`` field from ``dns_zones``
        (e.g. ``stdout.pt`` or ``1.168.192.in-addr.arpa``). The record list
        API requires BOTH ``zone_name`` and ``domain_name`` (same value).
        """
        return await self._request(
            "SYNO.DNSServer.Zone.Record",
            "list",
            params={"zone_name": zone_name, "domain_name": zone_name},
        )

    async def dns_add_record(
        self,
        zone_name: str,
        rr_owner: str,
        rr_type: str,
        rr_ttl: str,
        rr_info: str,
    ) -> dict[str, Any]:
        """Add a DNS record to a zone.

        Args:
            zone_name: zone name (e.g. ``stdout.pt``).
            rr_owner: record owner/name (e.g. ``docker-1.stdout.pt.`` — note the trailing dot).
            rr_type: record type (A, AAAA, CNAME, MX, NS, TXT, SRV, PTR).
            rr_ttl: TTL in seconds (string).
            rr_info: record data (e.g. ``192.168.1.110`` for A records).

        The create API takes a flat params object (verified from the DSM UI's
        own JS: CREATE mode sends zone_name/domain_name/rr_owner/rr_ttl/rr_type/rr_info
        directly, not wrapped in ``items``). Uses the SDK wire format (POST +
        JSON-stringified values) because the DNSServer API rejects string-typed
        params with ``reason: "type"``.
        """
        return await self._request_sdk(
            "SYNO.DNSServer.Zone.Record",
            "create",
            params={
                "zone_name": zone_name,
                "domain_name": zone_name,
                "rr_owner": rr_owner,
                "rr_type": rr_type,
                "rr_ttl": rr_ttl,
                "rr_info": rr_info,
            },
        )

    async def dns_delete_record(self, zone_name: str, record: dict[str, Any]) -> dict[str, Any]:
        """Delete a DNS record.

        Args:
            zone_name: zone name (e.g. ``stdout.pt``).
            record: the full record dict as returned by ``dns_records``
                (needs ``rr_owner``, ``rr_type``, ``rr_ttl``, ``rr_info``,
                ``full_record``).

        The delete API takes ``items`` — one entry per record, each carrying
        zone_name + domain_name + the record's rr_* fields (verified from the
        DSM UI's own JS).
        """
        item = {"zone_name": zone_name, "domain_name": zone_name}
        for k in ("rr_owner", "rr_type", "rr_ttl", "rr_info", "full_record"):
            if k in record:
                item[k] = record[k]
        return await self._request_sdk(
            "SYNO.DNSServer.Zone.Record", "delete", params={"items": [item]}
        )
