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
async def nfs_privilege_list() -> dict[str, Any]:
    """List NFS permission rules for shared folders.

    Returns:
        Per-share NFS privilege rules (server, privilege, squash, security, async).
    """
    client = await _client()
    try:
        return await client.nfs_privilege_list()
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
# Users / groups / accounts
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_users() -> dict[str, Any]:
    """List DSM local users.

    Returns:
        User list (name, description, email, admin flag, etc.).
    """
    client = await _client()
    try:
        return await client.user_list()
    finally:
        await client.aclose()


@mcp.tool()
async def get_user(name: str) -> dict[str, Any]:
    """Get details for one DSM user.

    Args:
        name: username (e.g. "admin", "mcp-server").

    Returns:
        That user's account details.
    """
    client = await _client()
    try:
        return await client.user_get(name)
    finally:
        await client.aclose()


@mcp.tool()
async def get_groups() -> dict[str, Any]:
    """List DSM local groups.

    Returns:
        Group list (name, description, gid, etc.).
    """
    client = await _client()
    try:
        return await client.group_list()
    finally:
        await client.aclose()


@mcp.tool()
async def get_group(name: str) -> dict[str, Any]:
    """Get details for one DSM group.

    Args:
        name: group name (e.g. "administrators", "users").

    Returns:
        That group's details.
    """
    client = await _client()
    try:
        return await client.group_get(name)
    finally:
        await client.aclose()


@mcp.tool()
async def get_user_home_settings() -> dict[str, Any]:
    """Get user home folder settings.

    Returns:
        Whether user homes are enabled and where they live.
    """
    client = await _client()
    try:
        return await client.user_home()
    finally:
        await client.aclose()


@mcp.tool()
async def get_password_policy() -> dict[str, Any]:
    """Get the DSM password strength policy.

    Returns:
        Minimum length, complexity rules, expiry, history.
    """
    client = await _client()
    try:
        return await client.user_password_policy()
    finally:
        await client.aclose()


@mcp.tool()
async def get_username_policy() -> dict[str, Any]:
    """Get the DSM username policy.

    Returns:
        Username constraints (length, allowed characters).
    """
    client = await _client()
    try:
        return await client.user_username_policy()
    finally:
        await client.aclose()


@mcp.tool()
async def get_password_expiry() -> dict[str, Any]:
    """Get per-user password expiry status.

    Returns:
        Users whose passwords are near/at expiry.
    """
    client = await _client()
    try:
        return await client.user_password_expiry()
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Network / DDNS / iSCSI
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_network_config() -> dict[str, Any]:
    """Get the DSM network configuration.

    Returns:
        Hostname, DNS servers, gateway, proxy, IP addressing.
    """
    client = await _client()
    try:
        return await client.network()
    finally:
        await client.aclose()


@mcp.tool()
async def get_network_interface_details() -> dict[str, Any]:
    """Get detailed per-interface network config.

    Returns:
        Interface list with IP, netmask, gateway, MTU, type.
    """
    client = await _client()
    try:
        return await client.network_interfaces()
    finally:
        await client.aclose()


@mcp.tool()
async def get_ovs_status() -> dict[str, Any]:
    """Get Open vSwitch (OVS) status.

    Returns:
        Whether OVS is enabled and its config.
    """
    client = await _client()
    try:
        return await client.network_ovs()
    finally:
        await client.aclose()


@mcp.tool()
async def get_ddns_records() -> dict[str, Any]:
    """List DDNS hostname records.

    Returns:
        Configured DDNS hostnames and their status.
    """
    client = await _client()
    try:
        return await client.ddns_records()
    finally:
        await client.aclose()


@mcp.tool()
async def get_ddns_providers() -> dict[str, Any]:
    """List supported DDNS providers.

    Returns:
        Provider names and capabilities.
    """
    client = await _client()
    try:
        return await client.ddns_providers()
    finally:
        await client.aclose()


@mcp.tool()
async def get_ddns_external_ip() -> dict[str, Any]:
    """Get the external IP as seen by DSM's DDNS service.

    Returns:
        Current external IP address.
    """
    client = await _client()
    try:
        return await client.ddns_extip()
    finally:
        await client.aclose()


@mcp.tool()
async def get_iscsi_luns() -> dict[str, Any]:
    """List iSCSI LUNs.

    Returns:
        LUN list with name, size, target, status.
    """
    client = await _client()
    try:
        return await client.iscsi_luns()
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Security / connections / TLS
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_firewall() -> dict[str, Any]:
    """Get the DSM firewall status.

    Returns:
        Whether the firewall is enabled and which profile is active.
    """
    client = await _client()
    try:
        return await client.firewall()
    finally:
        await client.aclose()


@mcp.tool()
async def get_firewall_profiles() -> dict[str, Any]:
    """List firewall profiles.

    Returns:
        Profile list with active status.
    """
    client = await _client()
    try:
        return await client.firewall_profiles()
    finally:
        await client.aclose()


@mcp.tool()
async def get_firewall_geoip() -> dict[str, Any]:
    """List GeoIP firewall rules.

    Returns:
        Country allow/deny rules.
    """
    client = await _client()
    try:
        return await client.firewall_geoip()
    finally:
        await client.aclose()


@mcp.tool()
async def get_autoblock() -> dict[str, Any]:
    """Get Auto Block (login attempt protection) settings.

    Returns:
        Enable flag, attempt threshold, block/expiry minutes.
    """
    client = await _client()
    try:
        return await client.autoblock()
    finally:
        await client.aclose()


@mcp.tool()
async def get_dsm_proxy() -> dict[str, Any]:
    """Get DSM reverse-proxy settings.

    Returns:
        Proxy configuration.
    """
    client = await _client()
    try:
        return await client.dsm_proxy()
    finally:
        await client.aclose()


@mcp.tool()
async def get_terminal_status() -> dict[str, Any]:
    """Get SSH/Telnet (Terminal) service status.

    Returns:
        Whether SSH and Telnet are enabled, and the SSH port.
    """
    client = await _client()
    try:
        return await client.terminal()
    finally:
        await client.aclose()


@mcp.tool()
async def get_smart_block() -> dict[str, Any]:
    """Get Smart Block (account protection) settings.

    Returns:
        Enable flag and thresholds.
    """
    client = await _client()
    try:
        return await client.smartblock()
    finally:
        await client.aclose()


@mcp.tool()
async def get_smart_block_trusted() -> dict[str, Any]:
    """List Smart Block trusted IPs/accounts.

    Returns:
        Trusted allow-list entries.
    """
    client = await _client()
    try:
        return await client.smartblock_trusted()
    finally:
        await client.aclose()


@mcp.tool()
async def get_smart_block_untrusted() -> dict[str, Any]:
    """List Smart Block blocked IPs/accounts.

    Returns:
        Blocked entries.
    """
    client = await _client()
    try:
        return await client.smartblock_untrusted()
    finally:
        await client.aclose()


@mcp.tool()
async def get_current_connections() -> dict[str, Any]:
    """List current DSM connections.

    Returns:
        Active connections with source IP, user, and application.
    """
    client = await _client()
    try:
        return await client.current_connections()
    finally:
        await client.aclose()


@mcp.tool()
async def get_connections_by_user() -> dict[str, Any]:
    """List current connections grouped by user.

    Returns:
        Per-user connection counts and details.
    """
    client = await _client()
    try:
        return await client.current_connections_by_user()
    finally:
        await client.aclose()


@mcp.tool()
async def get_tls_profile() -> dict[str, Any]:
    """Get the DSM TLS security profile.

    Returns:
        TLS version and cipher-suite settings.
    """
    client = await _client()
    try:
        return await client.tls_profile()
    finally:
        await client.aclose()


@mcp.tool()
async def get_web_dsm_settings() -> dict[str, Any]:
    """Get DSM web portal settings (HTTP/HTTPS ports, redirect).

    Returns:
        Web service port and HTTPS configuration.
    """
    client = await _client()
    try:
        return await client.web_dsm()
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Services / packages
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_services() -> dict[str, Any]:
    """List DSM services and their running/enabled state.

    Returns:
        Service list (name, running, enabled).
    """
    client = await _client()
    try:
        return await client.services()
    finally:
        await client.aclose()


@mcp.tool()
async def get_service_ports() -> dict[str, Any]:
    """Get the DSM services and their listening ports.

    Returns:
        Service-to-port mapping.
    """
    client = await _client()
    try:
        return await client.service_ports()
    finally:
        await client.aclose()


@mcp.tool()
async def get_package_servers() -> dict[str, Any]:
    """List configured package sources/repositories.

    Returns:
        Package server URLs and status.
    """
    client = await _client()
    try:
        return await client.package_servers()
    finally:
        await client.aclose()


@mcp.tool()
async def get_package_feeds() -> dict[str, Any]:
    """List package feeds (beta channel etc.).

    Returns:
        Feed configuration.
    """
    client = await _client()
    try:
        return await client.package_feeds()
    finally:
        await client.aclose()


@mcp.tool()
async def get_package_settings() -> dict[str, Any]:
    """Get Package Center settings.

    Returns:
        Auto-update and install-volume settings.
    """
    client = await _client()
    try:
        return await client.package_setting()
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Tasks / upgrade / hardware
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_scheduled_tasks() -> dict[str, Any]:
    """List scheduled tasks (Task Scheduler).

    Returns:
        Scheduled tasks with schedule, enabled state, last run.
    """
    client = await _client()
    try:
        return await client.task_scheduler_list()
    finally:
        await client.aclose()


@mcp.tool()
async def get_event_scheduler() -> dict[str, Any]:
    """List event scheduler rules (power on/off schedule).

    Returns:
        Event scheduler configuration.
    """
    client = await _client()
    try:
        return await client.event_scheduler_list()
    finally:
        await client.aclose()


@mcp.tool()
async def get_upgrade_status() -> dict[str, Any]:
    """Get DSM update status.

    Returns:
        Current version and whether an update is available.
    """
    client = await _client()
    try:
        return await client.upgrade_status()
    finally:
        await client.aclose()


@mcp.tool()
async def get_upgrade_settings() -> dict[str, Any]:
    """Get DSM update settings.

    Returns:
        Auto-update and update-channel configuration.
    """
    client = await _client()
    try:
        return await client.upgrade_setting()
    finally:
        await client.aclose()


@mcp.tool()
async def get_autoupgrade_security() -> dict[str, Any]:
    """Get security auto-update status.

    Returns:
        Whether critical security patches auto-apply.
    """
    client = await _client()
    try:
        return await client.autoupgrade_security()
    finally:
        await client.aclose()


@mcp.tool()
async def get_reboot_required() -> dict[str, Any]:
    """Check whether DSM needs a reboot.

    Returns:
        Whether a reboot is pending (e.g. after an update).
    """
    client = await _client()
    try:
        return await client.need_reboot()
    finally:
        await client.aclose()


@mcp.tool()
async def get_ups_status() -> dict[str, Any]:
    """Get UPS status (if a UPS is attached).

    Returns:
        UPS model, battery charge, load, status.
    """
    client = await _client()
    try:
        return await client.ups()
    finally:
        await client.aclose()


@mcp.tool()
async def get_external_usb_devices() -> dict[str, Any]:
    """List external USB storage devices.

    Returns:
        USB device list (empty if none attached).
    """
    client = await _client()
    try:
        return await client.external_usb()
    finally:
        await client.aclose()


@mcp.tool()
async def get_external_esata_devices() -> dict[str, Any]:
    """List external eSATA storage devices.

    Returns:
        eSATA device list (empty if none attached).
    """
    client = await _client()
    try:
        return await client.external_esata()
    finally:
        await client.aclose()


@mcp.tool()
async def get_external_storage_settings() -> dict[str, Any]:
    """Get external storage (eSATA/USB) settings.

    Returns:
        Eject/enable policy for external drives.
    """
    client = await _client()
    try:
        return await client.external_storage_setting()
    finally:
        await client.aclose()


@mcp.tool()
async def get_printers() -> dict[str, Any]:
    """List connected USB/network printers.

    Returns:
        Printer list (empty if none attached).
    """
    client = await _client()
    try:
        return await client.printers()
    finally:
        await client.aclose()


@mcp.tool()
async def get_power_schedule() -> dict[str, Any]:
    """Get the power on/off schedule.

    Returns:
        Scheduled power events.
    """
    client = await _client()
    try:
        return await client.power_schedule()
    finally:
        await client.aclose()


@mcp.tool()
async def get_power_recovery() -> dict[str, Any]:
    """Get power-recovery settings (behavior after power loss).

    Returns:
        What DSM does when power is restored.
    """
    client = await _client()
    try:
        return await client.power_recovery()
    finally:
        await client.aclose()


@mcp.tool()
async def get_beep_control() -> dict[str, Any]:
    """Get beep-control settings (audible alerts).

    Returns:
        Beep enable/disable state.
    """
    client = await _client()
    try:
        return await client.beep_control()
    finally:
        await client.aclose()


@mcp.tool()
async def get_memory_layout() -> dict[str, Any]:
    """Get memory module layout.

    Returns:
        Per-slot memory module info.
    """
    client = await _client()
    try:
        return await client.memory_layout()
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# File services (SMB / AFP / NFS / FTP / rsync)
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_smb_settings() -> dict[str, Any]:
    """Get SMB/CIFS file-service settings.

    Returns:
        SMB enable flag, workgroup, version, and options.
    """
    client = await _client()
    try:
        return await client.fileserv_smb()
    finally:
        await client.aclose()


@mcp.tool()
async def get_afp_settings() -> dict[str, Any]:
    """Get AFP (Apple Filing Protocol) settings.

    Returns:
        AFP enable flag and options.
    """
    client = await _client()
    try:
        return await client.fileserv_afp()
    finally:
        await client.aclose()


@mcp.tool()
async def get_nfs_settings() -> dict[str, Any]:
    """Get NFS file-service settings.

    Returns:
        NFS enable flag and versions.
    """
    client = await _client()
    try:
        return await client.fileserv_nfs()
    finally:
        await client.aclose()


@mcp.tool()
async def get_ftp_settings() -> dict[str, Any]:
    """Get FTP file-service settings.

    Returns:
        FTP enable flag, port, and options.
    """
    client = await _client()
    try:
        return await client.fileserv_ftp()
    finally:
        await client.aclose()


@mcp.tool()
async def get_rsync_accounts() -> dict[str, Any]:
    """List rsync backup accounts.

    Returns:
        rsync account list.
    """
    client = await _client()
    try:
        return await client.fileserv_rsync_accounts()
    finally:
        await client.aclose()


@mcp.tool()
async def get_reflink_copy_settings() -> dict[str, Any]:
    """Get reflink copy (fast-clone) settings.

    Returns:
        Reflink copy enable state.
    """
    client = await _client()
    try:
        return await client.fileserv_reflink()
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Logs / monitoring / region / misc
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_log_receive_rules() -> dict[str, Any]:
    """List Log Center receive rules.

    Returns:
        Remote log receive configuration.
    """
    client = await _client()
    try:
        return await client.logcenter_recv_rules()
    finally:
        await client.aclose()


@mcp.tool()
async def get_resource_monitor_settings() -> dict[str, Any]:
    """Get Resource Monitor settings.

    Returns:
        Monitoring thresholds and history settings.
    """
    client = await _client()
    try:
        return await client.resourcemonitor_setting()
    finally:
        await client.aclose()


@mcp.tool()
async def get_resource_monitor_rules() -> dict[str, Any]:
    """List Resource Monitor event rules (alerts).

    Returns:
        Alert rules with thresholds.
    """
    client = await _client()
    try:
        return await client.resourcemonitor_event_rules()
    finally:
        await client.aclose()


@mcp.tool()
async def get_resource_monitor_logs() -> dict[str, Any]:
    """List Resource Monitor log entries.

    Returns:
        Recent resource events.
    """
    client = await _client()
    try:
        return await client.resourcemonitor_logs()
    finally:
        await client.aclose()


@mcp.tool()
async def get_syslog_logs() -> dict[str, Any]:
    """List syslog client logs.

    Returns:
        Syslog log entries.
    """
    client = await _client()
    try:
        return await client.syslog_logs()
    finally:
        await client.aclose()


@mcp.tool()
async def get_ntp_settings() -> dict[str, Any]:
    """Get NTP time-sync settings.

    Returns:
        NTP server and timezone configuration.
    """
    client = await _client()
    try:
        return await client.ntp()
    finally:
        await client.aclose()


@mcp.tool()
async def get_ntp_status() -> dict[str, Any]:
    """Get NTP sync status.

    Returns:
        Whether time is synchronized.
    """
    client = await _client()
    try:
        return await client.ntp_status()
    finally:
        await client.aclose()


@mcp.tool()
async def get_region_language() -> dict[str, Any]:
    """Get regional/language settings.

    Returns:
        Language and locale configuration.
    """
    client = await _client()
    try:
        return await client.region_language()
    finally:
        await client.aclose()


@mcp.tool()
async def get_quickconnect_status() -> dict[str, Any]:
    """Get QuickConnect status.

    Returns:
        Whether QuickConnect is enabled and its ID.
    """
    client = await _client()
    try:
        return await client.quickconnect()
    finally:
        await client.aclose()


@mcp.tool()
async def get_media_indexing_status() -> dict[str, Any]:
    """Get media indexing status.

    Returns:
        Whether indexing is running and progress.
    """
    client = await _client()
    try:
        return await client.media_indexing_status()
    finally:
        await client.aclose()


@mcp.tool()
async def get_media_indexing_folders() -> dict[str, Any]:
    """List media indexing folders.

    Returns:
        Indexed folders and their types.
    """
    client = await _client()
    try:
        return await client.media_indexing_folders()
    finally:
        await client.aclose()


@mcp.tool()
async def get_certificates() -> dict[str, Any]:
    """List TLS certificates installed on DSM.

    Returns:
        Certificate list with issuer, expiry, and associated services.
    """
    client = await _client()
    try:
        return await client.certificates()
    finally:
        await client.aclose()


@mcp.tool()
async def get_snmp_settings() -> dict[str, Any]:
    """Get SNMP settings.

    Returns:
        SNMP enable flag and community/config.
    """
    client = await _client()
    try:
        return await client.snmp()
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
# Write tools (system management) — gated behind SYNOLOGY_ALLOW_WRITE
# ---------------------------------------------------------------------------


@mcp.tool()
async def group_create(name: str, description: str = "") -> dict[str, Any]:
    """Create a DSM local group (DESTRUCTIVE — requires SYNOLOGY_ALLOW_WRITE=true).

    Args:
        name: new group name.
        description: optional group description.

    Returns:
        Confirmation with the created group name.
    """
    _require_write()
    client = await _client()
    try:
        result = await client.group_create(name, description)
        return {"success": True, "name": name, "response": result}
    finally:
        await client.aclose()


@mcp.tool()
async def group_delete(name: str) -> dict[str, Any]:
    """Delete a DSM local group (DESTRUCTIVE — requires SYNOLOGY_ALLOW_WRITE=true).

    Args:
        name: group name to delete.

    Returns:
        Confirmation with the deleted group name.
    """
    _require_write()
    client = await _client()
    try:
        result = await client.group_delete(name)
        return {"success": True, "name": name, "response": result}
    finally:
        await client.aclose()


@mcp.tool()
async def share_create(
    name: str, vol_path: str = "/volume1", description: str = ""
) -> dict[str, Any]:
    """Create a shared folder (DESTRUCTIVE — requires SYNOLOGY_ALLOW_WRITE=true).

    Args:
        name: new share name.
        vol_path: volume path (e.g. "/volume1").
        description: optional description.

    Returns:
        Confirmation with the created share name.
    """
    _require_write()
    client = await _client()
    try:
        result = await client.share_create(name, vol_path, description=description)
        return {"success": True, "name": name, "response": result}
    finally:
        await client.aclose()


@mcp.tool()
async def share_delete(name: str) -> dict[str, Any]:
    """Delete a shared folder and its contents (DESTRUCTIVE — requires SYNOLOGY_ALLOW_WRITE=true)."""
    _require_write()
    client = await _client()
    try:
        result = await client.share_delete(name)
        return {"success": True, "name": name, "response": result}
    finally:
        await client.aclose()


@mcp.tool()
async def nfs_privilege_set(share_name: str, rules: list[dict[str, Any]]) -> dict[str, Any]:
    """Set NFS permission rules for a shared folder (DESTRUCTIVE — requires SYNOLOGY_ALLOW_WRITE=true).

    Args:
        share_name: the share to configure.
        rules: list of rule dicts, e.g.
            {"server": "192.168.1.0/24", "privilege": "rw", "squash": "all_squash",
             "security": "sys", "async": True}.

    Returns:
        Confirmation with the configured share name.
    """
    _require_write()
    client = await _client()
    try:
        result = await client.nfs_privilege_set(share_name, rules)
        return {"success": True, "share_name": share_name, "response": result}
    finally:
        await client.aclose()


@mcp.tool()
async def set_terminal(
    enable_ssh: bool,
    ssh_port: int = 22,
    enable_telnet: bool | None = None,
    forbid_console: bool | None = None,
) -> dict[str, Any]:
    """Enable/disable SSH and Telnet (DESTRUCTIVE — requires SYNOLOGY_ALLOW_WRITE=true).

    Args:
        enable_ssh: whether to enable the SSH service.
        ssh_port: SSH port (default 22).
        enable_telnet: optional — whether to enable Telnet.
        forbid_console: optional — whether to forbid console login.

    Returns:
        Confirmation of the terminal service state.
    """
    _require_write()
    client = await _client()
    try:
        result = await client.terminal_set(
            enable_ssh, ssh_port, enable_telnet=enable_telnet, forbid_console=forbid_console
        )
        return {"success": True, "enable_ssh": enable_ssh, "ssh_port": ssh_port, "response": result}
    finally:
        await client.aclose()


@mcp.tool()
async def service_control(service_id: str, action: str) -> dict[str, Any]:
    """Start/stop/restart a DSM service (DESTRUCTIVE — requires SYNOLOGY_ALLOW_WRITE=true).

    Args:
        service_id: the service's ``service_id`` from get_services (e.g. "ssh-shell").
        action: one of "start", "stop", "restart".

    Returns:
        Confirmation of the action applied.
    """
    _require_write()
    if action not in ("start", "stop", "restart"):
        raise SynologyError(f"action must be start/stop/restart, got {action!r}")
    client = await _client()
    try:
        result = await client.service_control(service_id, action)
        return {"success": True, "service_id": service_id, "action": action, "response": result}
    finally:
        await client.aclose()


@mcp.tool()
async def task_set_enable(task_id: int, enabled: bool) -> dict[str, Any]:
    """Enable/disable a scheduled task (DESTRUCTIVE — requires SYNOLOGY_ALLOW_WRITE=true).

    Args:
        task_id: the task's ``id`` from get_scheduled_tasks.
        enabled: whether the task should be enabled.

    Returns:
        Confirmation of the task's new state.
    """
    _require_write()
    client = await _client()
    try:
        result = await client.task_set_enable(task_id, enabled)
        return {"success": True, "task_id": task_id, "enabled": enabled, "response": result}
    finally:
        await client.aclose()


@mcp.tool()
async def package_control(package_id: str, action: str) -> dict[str, Any]:
    """Start/stop an installed package (DESTRUCTIVE — requires SYNOLOGY_ALLOW_WRITE=true).

    Args:
        package_id: the package's ``id`` from get_installed_packages.
        action: "start" or "stop".

    Returns:
        Confirmation of the action applied.
    """
    _require_write()
    client = await _client()
    try:
        result = await client.package_control(package_id, action)
        return {"success": True, "package_id": package_id, "action": action, "response": result}
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
