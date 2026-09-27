"""Main entry point for the Synology DSM MCP Server."""

from __future__ import annotations

import importlib.metadata
import os
from typing import Any, Literal, cast

from fastmcp import FastMCP

from .auth import StaticTokenAuth
from .client import SynologyClient, SynologyError
from .config import Settings

settings = Settings()

_auth_provider = StaticTokenAuth(settings.mcp_auth_token) if settings.mcp_auth_token else None
mcp = FastMCP("Synology DSM MCP Server", auth=_auth_provider)

if _auth_provider is not None:
    print("MCP bearer-token authentication enabled")
else:
    print("WARNING: MCP auth is DISABLED (MCP_AUTH_TOKEN not set) - server is unauthenticated")


async def _client() -> SynologyClient:
    """Create and authenticate a Synology client, raising a clean error on failure."""
    client = SynologyClient(
        settings.synology_url,
        settings.synology_username,
        settings.synology_password,
        auth_version=settings.synology_auth_version,
        timeout=settings.request_timeout,
        verify=False,  # DSM self-signed cert on the LAN
    )
    try:
        await client.authenticate()
    except Exception as exc:  # noqa: BLE001 - surface as tool error
        await client.aclose()
        raise SynologyError(
            f"Failed to connect/authenticate to DSM at {settings.synology_url}: {exc}"
        ) from exc
    return client


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def health_check() -> dict[str, str]:
    """Health check to verify the server is running and can reach DSM.

    Returns:
        Status information plus DSM connectivity.
    """
    try:
        client = await _client()
    except SynologyError as exc:
        return {
            "status": "unhealthy",
            "error": str(exc),
            "version": importlib.metadata.version("synology-mcp-server"),
        }
    try:
        await client.system_info()
        dsm_reachable = "ok"
    except SynologyError as exc:
        dsm_reachable = f"error: {exc}"
    finally:
        await client.aclose()
    return {
        "status": "healthy" if dsm_reachable == "ok" else "unhealthy",
        "dsm_reachable": dsm_reachable,
        "synology_url": settings.synology_url,
        "version": importlib.metadata.version("synology-mcp-server"),
    }


@mcp.tool()
async def get_system_info() -> dict[str, Any]:
    """Get DSM system info: model, serial, firmware version, CPU, RAM, temperature, uptime.

    Returns:
        Hardware identity plus basic health fields.
    """
    client = await _client()
    try:
        return await client.system_info()
    finally:
        await client.aclose()


@mcp.tool()
async def get_utilization() -> dict[str, Any]:
    """Get live CPU, memory, and network utilization.

    Returns:
        Per-core and aggregate CPU load, memory used/total, network rx/tx.
    """
    client = await _client()
    try:
        return await client.utilization()
    finally:
        await client.aclose()


@mcp.tool()
async def get_storage_overview() -> dict[str, Any]:
    """Get storage summary: disks, volumes, storage pools, SMART status, temperature.

    This is the highest-value read-only tool — one call returns the whole
    storage picture (every disk with model, size, SMART health, temp and
    remaining life, plus every volume/pool with capacity).

    Returns:
        ``load_info`` payload: disk list, volume list, storage pool list.
    """
    client = await _client()
    try:
        return await client.storage_load_info()
    finally:
        await client.aclose()


@mcp.tool()
async def get_fan_speed() -> dict[str, Any]:
    """Get current fan status and mode.

    Note: this model (RS819) reports fan presence/mode flags, not RPM.

    Returns:
        Fan presence, mode (e.g. quiet/dual), and disk-temperature alarm flag.
    """
    client = await _client()
    try:
        return await client.fan_speed()
    finally:
        await client.aclose()


@mcp.tool()
async def get_network_interfaces() -> dict[str, Any]:
    """Get Ethernet interfaces: IP, MAC, link speed, MTU, status.

    Returns:
        Network interface list with addressing and link state.
    """
    client = await _client()
    try:
        interfaces = await client.network_ethernet()
        # DSM returns a bare list; wrap so FastMCP gets a dict.
        return {"interfaces": interfaces}
    finally:
        await client.aclose()


@mcp.tool()
async def get_installed_packages() -> dict[str, Any]:
    """Get installed DSM packages with running status.

    Returns:
        Package list with version and running/enabled state.
    """
    client = await _client()
    try:
        return await client.packages()
    finally:
        await client.aclose()


@mcp.tool()
async def get_dsm_info() -> dict[str, Any]:
    """Get DSM version and edition information.

    Returns:
        DSM build/version/edition details.
    """
    client = await _client()
    try:
        return await client.dsm_info()
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Read-only expansion: DNS, shares, processes, logs, hardware
# ---------------------------------------------------------------------------


@mcp.tool()
async def dns_list_zones() -> dict[str, Any]:
    """List DNS Server zones (forward and reverse) with type and read-only status.

    Returns:
        Each zone's name, type (forward/reverse), master/slave, enabled, readonly.
    """
    client = await _client()
    try:
        return await client.dns_zones()
    finally:
        await client.aclose()


@mcp.tool()
async def dns_list_records(zone_name: str) -> dict[str, Any]:
    """List all records in a DNS zone (A, CNAME, MX, NS, TXT, PTR, etc.).

    Args:
        zone_name: the zone name from dns_list_zones (e.g. "stdout.pt").

    Returns:
        ``items``: each record's rr_owner, rr_type, rr_ttl, rr_info, full_record.
    """
    client = await _client()
    try:
        return await client.dns_records(zone_name)
    finally:
        await client.aclose()


@mcp.tool()
async def dns_list_views() -> dict[str, Any]:
    """List DNS Server views.

    Returns:
        Configured DNS views.
    """
    client = await _client()
    try:
        return await client.dns_views()
    finally:
        await client.aclose()


@mcp.tool()
async def dns_daemon_status() -> dict[str, Any]:
    """Get DNS Server daemon status (recursive/tcp clients, memory).

    Returns:
        Recursive-client count, TCP-client count, memory alert flag.
    """
    client = await _client()
    try:
        return await client.dns_daemon_status()
    finally:
        await client.aclose()


@mcp.tool()
async def get_shares() -> dict[str, Any]:
    """List shared folders.

    Returns:
        Shared folder list with name, path, and attributes.
    """
    client = await _client()
    try:
        return await client.shares()
    finally:
        await client.aclose()


@mcp.tool()
async def get_system_processes() -> dict[str, Any]:
    """List running system processes.

    Returns:
        Process list.
    """
    client = await _client()
    try:
        return await client.system_processes()
    finally:
        await client.aclose()


@mcp.tool()
async def get_logcenter_logs() -> dict[str, Any]:
    """List recent Log Center log entries.

    Returns:
        Log entries with timestamp, level, and message.
    """
    client = await _client()
    try:
        return await client.logcenter_logs()
    finally:
        await client.aclose()


@mcp.tool()
async def get_network_bonds() -> dict[str, Any]:
    """List network bond interfaces.

    Returns:
        Bond interface configuration.
    """
    client = await _client()
    try:
        bonds = await client.network_bonds()
        # DSM returns a bare list; wrap so FastMCP gets a dict.
        return {"bonds": bonds}
    finally:
        await client.aclose()


@mcp.tool()
async def get_hibernation_settings() -> dict[str, Any]:
    """Get disk hibernation settings and current status.

    Returns:
        Hibernation enable flags, idle times, and supported devices.
    """
    client = await _client()
    try:
        return await client.hibernation()
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Write tools (DNS records) — gated behind SYNOLOGY_ALLOW_WRITE
# ---------------------------------------------------------------------------


def _require_write() -> None:
    if not settings.allow_write:
        raise SynologyError(
            "Write tools are disabled. Set SYNOLOGY_ALLOW_WRITE=true to enable them."
        )


@mcp.tool()
async def dns_add_record(
    zone_name: str, rr_owner: str, rr_type: str, rr_ttl: str, rr_info: str
) -> dict[str, Any]:
    """Add a DNS record to a zone (DESTRUCTIVE — requires SYNOLOGY_ALLOW_WRITE=true).

    Args:
        zone_name: zone name (e.g. "stdout.pt").
        rr_owner: record owner/name (e.g. "newhost.stdout.pt." — trailing dot).
        rr_type: record type (A, AAAA, CNAME, MX, NS, TXT, SRV, PTR).
        rr_ttl: TTL in seconds (string, e.g. "86400").
        rr_info: record data (e.g. "192.168.1.50" for A records).

    Returns:
        The API response after creating the record.
    """
    _require_write()
    client = await _client()
    try:
        result = await client.dns_add_record(zone_name, rr_owner, rr_type, rr_ttl, rr_info)
        return {
            "success": True,
            "zone_name": zone_name,
            "rr_owner": rr_owner,
            "rr_type": rr_type,
            "response": result,
        }
    finally:
        await client.aclose()


@mcp.tool()
async def dns_delete_record(zone_name: str, record: dict[str, Any]) -> dict[str, Any]:
    """Delete a DNS record (DESTRUCTIVE — requires SYNOLOGY_ALLOW_WRITE=true).

    Args:
        zone_name: zone name (e.g. "stdout.pt").
        record: the full record dict from dns_list_records (must include
            rr_owner, rr_type, rr_ttl, rr_info, full_record).

    Returns:
        The API response after deleting the record.
    """
    _require_write()
    client = await _client()
    try:
        result = await client.dns_delete_record(zone_name, record)
        return {
            "success": True,
            "zone_name": zone_name,
            "rr_owner": record.get("rr_owner"),
            "response": result,
        }
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Main entry point for the MCP server."""
    import uvicorn
    from starlette.middleware.cors import CORSMiddleware

    transport = os.getenv("FASTMCP_TRANSPORT", "stdio")
    host = os.getenv("FASTMCP_HOST", "0.0.0.0")
    port = int(os.getenv("FASTMCP_PORT", "3101"))

    print(f"Starting Synology DSM MCP Server (transport={transport}, port={port})")
    print(f"DSM URL: {settings.synology_url}")

    if transport in ("streamable-http", "http", "sse"):
        http_transport = cast(Literal["http", "streamable-http", "sse"], transport)
        starlette_app = mcp.http_app(transport=http_transport)
        cors_app = CORSMiddleware(
            app=starlette_app,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
        uvicorn.run(cors_app, host=host, port=port, lifespan="on")
    else:
        run_transport = cast(Literal["stdio", "http", "sse", "streamable-http"], transport)
        mcp.run(transport=run_transport)


if __name__ == "__main__":
    main()
