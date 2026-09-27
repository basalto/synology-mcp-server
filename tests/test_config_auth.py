"""Unit tests for configuration and auth."""

from __future__ import annotations

import asyncio

import pytest

from synology_mcp.auth import StaticTokenAuth
from synology_mcp.config import Settings


def test_static_token_auth_accepts_matching_token():
    auth = StaticTokenAuth("sekret")
    result = asyncio.run(auth.verify_token("sekret"))
    assert result is not None
    assert result.token == "sekret"


def test_static_token_auth_rejects_wrong_token():
    auth = StaticTokenAuth("sekret")
    assert asyncio.run(auth.verify_token("wrong")) is None


def test_static_token_auth_rejects_empty():
    auth = StaticTokenAuth("sekret")
    assert asyncio.run(auth.verify_token("")) is None


def test_static_token_auth_requires_nonempty_token():
    with pytest.raises(ValueError):
        StaticTokenAuth("")


def test_settings_loads_validation_aliases(monkeypatch):
    monkeypatch.setenv("SYNOLOGY_URL", "http://10.0.0.9:5000")
    monkeypatch.setenv("SYNOLOGY_USERNAME", "svc-mcp")
    monkeypatch.setenv("SYNOLOGY_PASSWORD", "hunter2")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "tok")

    s = Settings(_env_file=None)

    assert s.synology_url == "http://10.0.0.9:5000"
    assert s.synology_username == "svc-mcp"
    assert s.synology_password == "hunter2"
    assert s.mcp_auth_token == "tok"


def test_settings_defaults():
    s = Settings(_env_file=None)
    assert s.synology_url == "http://192.168.1.93:5000"
    assert s.synology_auth_version == "6"
    assert s.request_timeout == 15.0
    assert s.mcp_auth_token is None
