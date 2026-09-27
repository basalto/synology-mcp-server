"""Unit tests for the Synology DSM API client."""

from __future__ import annotations

import pytest

from synology_mcp.client import SynologyClient, SynologyError


def _resp(payload: dict):
    """Plain (sync) httpx-style response: .json() is not awaited."""
    r = type("Resp", (), {})()
    r._payload = payload

    def json():
        return r._payload

    r.json = json
    return r


@pytest.fixture
async def client():
    c = SynologyClient(
        "http://192.168.1.93:5000",
        "user",
        "pass",
        timeout=5.0,
    )
    yield c
    await c.aclose()


async def test_authenticate_stores_sid(client, mocker):
    mocker.patch.object(
        client._client,
        "get",
        new=mocker.AsyncMock(return_value=_resp({"success": True, "data": {"sid": "abc123"}})),
    )

    sid = await client.authenticate()

    assert sid == "abc123"
    assert client._sid == "abc123"
    # login params carried the account/passwd + session/format
    _, kwargs = client._client.get.await_args
    assert kwargs["params"]["api"] == "SYNO.API.Auth"
    assert kwargs["params"]["method"] == "login"
    assert kwargs["params"]["account"] == "user"
    assert kwargs["params"]["passwd"] == "pass"


async def test_authenticate_rejects_login_failure(client, mocker):
    mocker.patch.object(
        client._client,
        "get",
        new=mocker.AsyncMock(return_value=_resp({"success": False, "error": {"code": 400}})),
    )

    with pytest.raises(SynologyError, match="login failed"):
        await client.authenticate()


async def test_authenticate_rejects_missing_sid(client, mocker):
    mocker.patch.object(
        client._client,
        "get",
        new=mocker.AsyncMock(return_value=_resp({"success": True, "data": {}})),
    )

    with pytest.raises(SynologyError, match="no session ID"):
        await client.authenticate()


async def test_request_carries_sid_param(client, mocker):
    client._sid = "abc123"
    mocker.patch.object(
        client._client,
        "get",
        new=mocker.AsyncMock(return_value=_resp({"success": True, "data": {"ok": True}})),
    )

    result = await client.system_info()

    assert result == {"ok": True}
    _, kwargs = client._client.get.await_args
    assert kwargs["params"]["_sid"] == "abc123"
    assert kwargs["params"]["api"] == "SYNO.Core.System"
    assert kwargs["params"]["method"] == "info"


async def test_request_reauths_on_119(client, mocker):
    client._sid = "stale"
    mocker.patch.object(
        client._client,
        "get",
        new=mocker.AsyncMock(
            side_effect=[
                # 1. initial call fails with expired session
                _resp({"success": False, "error": {"code": 119}}),
                # 2. authenticate() re-logs in
                _resp({"success": True, "data": {"sid": "fresh"}}),
                # 3. retry succeeds
                _resp({"success": True, "data": {"ok": True}}),
            ]
        ),
    )

    result = await client.system_info()

    assert result == {"ok": True}
    assert client._sid == "fresh"


async def test_request_raises_on_other_error_codes(client, mocker):
    client._sid = "abc123"
    mocker.patch.object(
        client._client,
        "get",
        new=mocker.AsyncMock(return_value=_resp({"success": False, "error": {"code": 102}})),
    )

    with pytest.raises(SynologyError, match="code 102"):
        await client.system_info()


async def test_logout_clears_sid(client, mocker):
    client._sid = "abc123"
    mocker.patch.object(client._client, "get", new=mocker.AsyncMock())

    await client.logout()

    _, kwargs = client._client.get.await_args
    assert kwargs["params"]["method"] == "logout"
    assert kwargs["params"]["_sid"] == "abc123"
    assert client._sid is None


async def test_logout_noop_without_sid(client, mocker):
    client._sid = None
    mocker.patch.object(client._client, "get", new=mocker.AsyncMock())

    await client.logout()

    client._client.get.assert_not_called()


async def test_storage_tools_call_right_apis(client, mocker):
    client._sid = "abc123"
    mocker.patch.object(
        client._client,
        "get",
        new=mocker.AsyncMock(return_value=_resp({"success": True, "data": {}})),
    )

    await client.storage_load_info()
    await client.fan_speed()
    await client.packages()
    await client.network_ethernet()

    called_apis = [c.kwargs["params"]["api"] for c in client._client.get.await_args_list]
    assert called_apis == [
        "SYNO.Storage.CGI.Storage",
        "SYNO.Core.Hardware.FanSpeed",
        "SYNO.Core.Package",
        "SYNO.Core.Network.Ethernet",
    ]


async def test_dns_write_uses_sdk_wire_format(client, mocker):
    """DNSServer write methods must POST to /entry.cgi/{api} with every param
    value JSON-stringified (the modern SDK's z() serializer) — otherwise the
    API rejects params with reason:"type"."""
    client._sid = "abc123"
    post_mock = mocker.AsyncMock(return_value=_resp({"success": True, "data": {}}))
    mocker.patch.object(client._client, "post", new=post_mock)

    await client.dns_add_record("stdout.pt", "x.stdout.pt.", "A", "86400", "1.2.3.4")

    post_mock.assert_called_once()
    args, kwargs = post_mock.await_args
    assert args[0] == "http://192.168.1.93:5000/webapi/entry.cgi/SYNO.DNSServer.Zone.Record"
    body = kwargs["data"]
    assert body["method"] == "create"
    assert body["rr_owner"] == '"x.stdout.pt."'  # JSON-stringified (with quotes)
    assert body["rr_ttl"] == '"86400"'  # string -> quoted; ints would be bare
