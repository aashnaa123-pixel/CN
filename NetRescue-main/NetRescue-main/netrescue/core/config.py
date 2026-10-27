"""
netrescue/core/config.py

Environment-driven settings for NetRescue.
Loaded once at import; values read from environment variables or .env file.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings — all values can be overridden by environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Database ---
    database_url: str = Field(
        default="postgresql+psycopg://netrescue:netrescue@localhost:5432/netrescue",
        description="SQLAlchemy async-compatible PostgreSQL URL",
    )

    db_connect_timeout_s: int = Field(
        default=5,
        ge=1,
        le=120,
        description=(
            "Seconds to wait for a database connection before failing. Keeps a "
            "down database from stalling callers (PRD §2.2 rule 3)."
        ),
    )

    # --- API ---
    api_bind_host: str = Field(default="127.0.0.1", description="Host to bind FastAPI to (loopback by default)")
    api_bind_port: int = Field(default=8000, description="Port for the FastAPI server")
    api_secret_key: str = Field(default="change-me", description="Secret key for session / token signing")

    api_auth_token: str = Field(
        default="",
        description=(
            "Shared secret. When set, a request may only claim a role above 'readonly' "
            "by sending the same value in X-NetRescue-Token; otherwise it is treated as "
            "readonly. Empty keeps the loopback-only trust model. Set this before "
            "binding the API to any non-loopback address."
        ),
    )

    cors_origins: str = Field(
        default="",
        description=(
            "Comma-separated, exact browser origins (scheme://host[:port], no wildcard) "
            "allowed to call this API, e.g. the deployed frontend's URL. Local "
            "development origins are allowed in addition while CORS_ALLOW_LOOPBACK is true."
        ),
    )

    cors_allow_loopback: bool = Field(
        default=True,
        description=(
            "Also allow any http(s)://localhost, 127.0.0.1 or [::1] origin, so local "
            "Vite dev servers work on any port. Set false on a hosted API that should "
            "serve only the origins in CORS_ORIGINS."
        ),
    )

    public_demo: bool = Field(
        default=False,
        description=(
            "Hosted-demo mode. Callers without a valid X-NetRescue-Token may use the "
            "operator role ONLY on the simulation routes listed in "
            "netrescue.security.demo; everything else stays readonly. No secret ships "
            "to the browser. Leave false unless the API is a public demo."
        ),
    )

    # --- TLS / mTLS ---
    tls_ca_cert: str = Field(default="certs/ca.crt", description="Path to CA certificate")
    tls_node_cert: str = Field(default="certs/node.crt", description="Path to node certificate")
    tls_node_key: str = Field(default="certs/node.key", description="Path to node private key")

    # --- Heartbeat defaults ---
    heartbeat_interval_s: float = Field(default=1.0, description="Default UDP heartbeat interval in seconds")
    heartbeat_timeout_s: float = Field(default=5.0, description="Heartbeat receive timeout in seconds")

    # --- Hysteresis defaults ---
    default_f_thresh: int = Field(default=3, description="Missed heartbeats to enter SUSPECT")
    default_n: int = Field(default=5, description="Missed heartbeats to enter FAILED")
    default_r_thresh: int = Field(default=5, description="Consecutive successes to return to HEALTHY")

    # --- Feature flags ---
    enable_emulation: bool = Field(default=True, description="Enable tc/netem emulation module")
    enable_security: bool = Field(default=True, description="Enable mTLS and HMAC security")

    # --- PostgreSQL (Docker Compose) ---
    postgres_user: str = Field(default="netrescue")
    postgres_password: str = Field(default="netrescue")
    postgres_db: str = Field(default="netrescue")

    # --- Logging ---
    log_level: str = Field(default="INFO", description="Python logging level")


# Module-level singleton — import this everywhere
settings = Settings()
