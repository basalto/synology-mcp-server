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
