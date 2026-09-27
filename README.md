# Synology DSM MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server exposing the
Synology **DSM WebAPI** for the homelab's NAS3 (`192.168.1.93`).

Built with FastMCP, shipped as a Docker Swarm service (host networking, port
`3101`), same pattern as the `pihole-mcp-server` and `unifi-mcp-server`.

## Tools (11, all read-only)

| Tool | DSM API | Purpose |
|---|---|---|
| `health_check` | — | server + DSM reachability |
| `get_system_info` | `SYNO.Core.System.info` | model, serial, firmware, CPU, RAM, temp, uptime |
| `get_utilization` | `SYNO.Core.System.Utilization.get` | live CPU / memory / network |
| `get_storage_overview` | `SYNO.Storage.CGI.Storage.load_info` | **disks + volumes + pools + SMART + temp in one call** |
| `get_volumes` | `SYNO.Storage.CGI.Volume.list` | volume capacity/health |
| `get_smart_status` | `SYNO.Storage.CGI.Smart.get` | per-disk SMART pass/fail |
| `get_fan_speed` | `SYNO.Core.Hardware.FanSpeed.get` | fan RPM |
| `get_power_schedule` | `SYNO.Core.Hardware.PowerSchedule.load` | power on/off schedule |
| `get_network_interfaces` | `SYNO.Core.Network.Ethernet.list` | IP, MAC, link speed, MTU |
| `get_installed_packages` | `SYNO.Core.Package.list` | packages + running status |
| `get_dsm_info` | `SYNO.DSM.Info.getinfo` | DSM version/edition |

All tools are **read-only** — there is no write path, so the MCP server cannot
mutate the NAS. This is deliberate (matches the proxmox-mcp read-only posture).

## Configuration (environment)

| Var | Default | Purpose |
|---|---|---|
| `SYNOLOGY_URL` | `http://192.168.1.93:5000` | DSM base URL |
| `SYNOLOGY_USERNAME` | — | DSM account (use a dedicated least-privilege account) |
| `SYNOLOGY_PASSWORD` | — | DSM account password |
| `SYNOLOGY_AUTH_VERSION` | `6` | `SYNO.API.Auth` version (6 stable across DSM 6.2+/7.x) |
| `MCP_AUTH_TOKEN` | *(unset)* | optional bearer token for the MCP endpoint itself |
| `FASTMCP_TRANSPORT` | `stdio` | `streamable-http` when containerized |
| `FASTMCP_PORT` | `3101` | HTTP listen port |

## DSM API quirks (why the client is written the way it is)

1. **Errors use HTTP 200.** DSM reports failures as JSON `{"success": false,
   "error": {"code": ...}}` with a 200 status. The client inspects `success`,
   not the HTTP status.
2. **Error code 119 = session expired.** The client re-authenticates and retries
   once, transparently.
3. **Auth is login → sid**, not a long-lived API key: `SYNO.API.Auth.login`
   returns a `sid` carried as the `_sid` query param on every call. The client
   releases the session on close (DSM caps concurrent sessions per account).

## Run locally

```bash
uv venv .venv && . .venv/bin/activate
uv pip install -e ".[dev]"
FASTMCP_TRANSPORT=stdio SYNOLOGY_USERNAME=... SYNOLOGY_PASSWORD=... synology-mcp-server
```

## Test

```bash
uv run pytest tests/ -q
```
