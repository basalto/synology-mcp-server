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

    # ------------------------------------------------------------------
    # Users / groups / accounts
    # ------------------------------------------------------------------

    async def user_list(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.User", "list")

    async def user_get(self, name: str) -> dict[str, Any]:
        return await self._request("SYNO.Core.User", "get", params={"name": name})

    async def group_list(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Group", "list")

    async def group_get(self, name: str) -> dict[str, Any]:
        return await self._request("SYNO.Core.Group", "get", params={"name": name})

    async def user_home(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.User.Home", "get")

    async def user_password_policy(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.User.PasswordPolicy", "get")

    async def user_username_policy(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.User.UsernamePolicy", "list")

    async def user_password_expiry(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.User.PasswordExpiry", "get")

    # ------------------------------------------------------------------
    # Network / DDNS / iSCSI
    # ------------------------------------------------------------------

    async def network(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Network", "get")

    async def network_interfaces(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Network.Interface", "list")

    async def network_ovs(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Network.OVS", "get")

    async def ddns_records(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.DDNS.Record", "list")

    async def ddns_providers(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.DDNS.Provider", "list")

    async def ddns_extip(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.DDNS.ExtIP", "list")

    async def iscsi_luns(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.ISCSI.LUN", "list")

    # ------------------------------------------------------------------
    # Security / connections / TLS
    # ------------------------------------------------------------------

    async def firewall(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Security.Firewall", "get")

    async def firewall_profiles(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Security.Firewall.Profile", "list")

    async def firewall_geoip(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Security.Firewall.Geoip", "list")

    async def autoblock(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Security.AutoBlock", "get")

    async def dsm_proxy(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Security.DSM.Proxy", "get")

    async def terminal(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Terminal", "get", version="2")

    async def smartblock(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.SmartBlock", "get")

    async def smartblock_trusted(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.SmartBlock.Trusted", "list")

    async def smartblock_untrusted(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.SmartBlock.Untrusted", "list")

    async def current_connections(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.CurrentConnection", "get")

    async def current_connections_by_user(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.CurrentConnection", "list_by_user")

    async def tls_profile(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Web.Security.TLSProfile", "get")

    async def web_dsm(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Web.DSM", "get", version="2")

    # ------------------------------------------------------------------
    # Services / packages
    # ------------------------------------------------------------------

    async def services(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Service", "get", version="2")

    async def service_ports(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Service.PortInfo", "load")

    async def package_servers(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Package.Server", "list", version="2")

    async def package_feeds(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Package.Feed", "list")

    async def package_setting(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Package.Setting", "get")

    # ------------------------------------------------------------------
    # Tasks / upgrade / hardware
    # ------------------------------------------------------------------

    async def task_scheduler_list(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.TaskScheduler", "list", version="3")

    async def event_scheduler_list(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.EventScheduler", "list")

    async def upgrade_status(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Upgrade", "basic_status")

    async def upgrade_setting(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Upgrade.Setting", "get")

    async def autoupgrade_security(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Upgrade.AutoUpgrade.Security", "get")

    async def need_reboot(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Hardware.NeedReboot", "get")

    async def ups(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.ExternalDevice.UPS", "get")

    async def external_usb(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.ExternalDevice.Storage.USB", "list")

    async def external_esata(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.ExternalDevice.Storage.eSATA", "list")

    async def external_storage_setting(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.ExternalDevice.Storage.Setting", "get")

    async def printers(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.ExternalDevice.Printer", "list")

    async def power_schedule(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Hardware.PowerSchedule", "load")

    async def power_recovery(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Hardware.PowerRecovery", "get")

    async def beep_control(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Hardware.BeepControl", "get")

    async def memory_layout(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Hardware.MemoryLayout", "get")

    # ------------------------------------------------------------------
    # File services (SMB / AFP / NFS / FTP / rsync)
    # ------------------------------------------------------------------

    async def fileserv_smb(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.FileServ.SMB", "get")

    async def fileserv_afp(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.FileServ.AFP", "get")

    async def fileserv_nfs(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.FileServ.NFS", "get")

    async def fileserv_ftp(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.FileServ.FTP", "get")

    async def fileserv_rsync_accounts(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.FileServ.Rsync.Account", "list")

    async def fileserv_reflink(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.FileServ.ReflinkCopy", "get")

    # ------------------------------------------------------------------
    # Logs / monitoring / region / misc
    # ------------------------------------------------------------------

    async def logcenter_recv_rules(self) -> dict[str, Any]:
        return await self._request("SYNO.LogCenter.RecvRule", "list")

    async def resourcemonitor_setting(self) -> dict[str, Any]:
        return await self._request("SYNO.ResourceMonitor.Setting", "get")

    async def resourcemonitor_event_rules(self) -> dict[str, Any]:
        return await self._request("SYNO.ResourceMonitor.EventRule", "list")

    async def resourcemonitor_logs(self) -> dict[str, Any]:
        return await self._request("SYNO.ResourceMonitor.Log", "list")

    async def syslog_logs(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.SyslogClient.Log", "list")

    async def ntp(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Region.NTP", "get", version="3")

    async def ntp_status(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Region.NTP", "status")

    async def region_language(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Region.Language", "get")

    async def quickconnect(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.QuickConnect", "get", version="2")

    async def media_indexing_status(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.MediaIndexing", "status")

    async def media_indexing_folders(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.MediaIndexing.IndexFolder", "get")

    async def certificates(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.Certificate.CRT", "list")

    async def snmp(self) -> dict[str, Any]:
        return await self._request("SYNO.Core.SNMP", "get")

    # ------------------------------------------------------------------
    # DNS zone records (write) — gated upstream in main.py
    # ------------------------------------------------------------------

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
