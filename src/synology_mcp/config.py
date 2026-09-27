"""Configuration management for the Synology MCP Server using Pydantic Settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    synology_url: str = Field(
        default="http://192.168.1.93:5000",
        description="Synology DSM base URL (scheme://host:port, no trailing slash)",
        validation_alias="SYNOLOGY_URL",
    )

    synology_username: str = Field(
        default="",
        description="DSM account username (a dedicated least-privilege account is recommended)",
        validation_alias="SYNOLOGY_USERNAME",
    )

    synology_password: str = Field(
        default="",
        description="DSM account password",
        validation_alias="SYNOLOGY_PASSWORD",
    )

    synology_auth_version: str = Field(
        default="6",
        description="SYNO.API.Auth version to use for login (6 is stable across DSM 6.2+ / 7.x)",
        validation_alias="SYNOLOGY_AUTH_VERSION",
    )

    mcp_auth_token: str | None = Field(
        default=None,
        description="Optional bearer token required on HTTP transports",
        validation_alias="MCP_AUTH_TOKEN",
    )

    allow_write: bool = Field(
        default=False,
        description=(
            "Enable destructive/write tools (DNS record create/delete, etc.). "
            "Defaults to False (read-only). Set SYNOLOGY_ALLOW_WRITE=true to enable."
        ),
        validation_alias="SYNOLOGY_ALLOW_WRITE",
    )

    request_timeout: float = Field(
        default=15.0,
        description="HTTP request timeout in seconds",
        validation_alias="SYNOLOGY_REQUEST_TIMEOUT",
    )

    log_level: str = Field(
        default="INFO",
        description="Log level (DEBUG, INFO, WARNING, ERROR)",
        validation_alias="SYNOLOGY_LOG_LEVEL",
    )
