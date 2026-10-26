"""Validated settings with fail-closed loopback and local-storage defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

from config.errors import ConfigurationError, ConfigurationErrorCode

APP_NAME = "BenchFlow"
CANONICAL_HOST = "127.0.0.1"
CANONICAL_PORT = 8000
CANONICAL_ORIGIN = "http://127.0.0.1:8000"
DEFAULT_SHOP_TIMEZONE = "America/New_York"
SQLITE_BUSY_TIMEOUT_MS = 2000
MAX_JSON_BODY_BYTES = 256 * 1024

_ALLOWED_ENV = frozenset({
    "BENCHFLOW_HOST",
    "BENCHFLOW_PORT",
    "BENCHFLOW_DATA_ROOT",
    "BENCHFLOW_DEMO",
})


def _local_app_data_root(environ: Mapping[str, str] | None = None) -> Path:
    source = os.environ if environ is None else environ
    configured = source.get("LOCALAPPDATA")
    if configured:
        return Path(configured)
    return Path.home() / "AppData" / "Local"


@dataclass(frozen=True, slots=True)
class Settings:
    """Non-secret process configuration shared with Backend and Infrastructure.

    Host/port/origin are intentionally fixed for this delivery. Configurable
    bind addresses and remote exposure are not supported.
    """

    data_root: Path
    demo_mode: bool = False
    local_app_data_root: Path | None = None
    host: str = CANONICAL_HOST
    port: int = CANONICAL_PORT
    shop_timezone: str = DEFAULT_SHOP_TIMEZONE
    busy_timeout_ms: int = SQLITE_BUSY_TIMEOUT_MS
    max_json_body_bytes: int = MAX_JSON_BODY_BYTES

    def __post_init__(self) -> None:
        if self.host != CANONICAL_HOST or self.port != CANONICAL_PORT:
            raise ConfigurationError(
                ConfigurationErrorCode.UNSAFE_BIND,
                "BENCHFLOW_HOST/BENCHFLOW_PORT",
                "only 127.0.0.1:8000 is supported; no remote bind or port fallback",
            )
        if self.shop_timezone != DEFAULT_SHOP_TIMEZONE:
            raise ConfigurationError(
                ConfigurationErrorCode.INVALID_SETTING,
                "shop_timezone",
                f"the initial shop timezone is fixed to {DEFAULT_SHOP_TIMEZONE}",
            )
        if self.busy_timeout_ms != SQLITE_BUSY_TIMEOUT_MS:
            raise ConfigurationError(
                ConfigurationErrorCode.INVALID_SETTING, "busy_timeout_ms", "must be 2000 ms"
            )
        if self.max_json_body_bytes != MAX_JSON_BODY_BYTES:
            raise ConfigurationError(
                ConfigurationErrorCode.INVALID_SETTING, "max_json_body_bytes", "must be 262144 bytes"
            )
        raw_path = str(self.data_root)
        if not self.data_root.is_absolute() or raw_path.startswith("\\\\") or raw_path.startswith("//"):
            raise ConfigurationError(
                ConfigurationErrorCode.INVALID_DATA_ROOT,
                "BENCHFLOW_DATA_ROOT",
                "must be an absolute local path, not a UNC/network path",
            )
        # Canonicalization is non-creating; database initialization owns mkdir.
        object.__setattr__(self, "data_root", self.data_root.resolve(strict=False))
        local_root = self.local_app_data_root or _local_app_data_root()
        if (
            not local_root.is_absolute()
            or str(local_root).startswith("\\\\")
            or str(local_root).startswith("//")
        ):
            raise ConfigurationError(
                ConfigurationErrorCode.INVALID_DATA_ROOT,
                "LOCALAPPDATA",
                "must be an absolute local path, not a UNC path",
            )
        object.__setattr__(self, "local_app_data_root", local_root.resolve(strict=False))

    @property
    def origin(self) -> str:
        return CANONICAL_ORIGIN

    @property
    def database_path(self) -> Path:
        return self.data_root / "benchflow.sqlite3"

    @property
    def backup_root(self) -> Path:
        # The backup directory is fixed to the current user's local application
        # data root and is not caller- or browser-selectable.
        assert self.local_app_data_root is not None
        return self.local_app_data_root / "BenchFlow" / "backups"

    def for_staged_root(self, data_root: Path) -> "Settings":
        """Clone this configuration for an isolated same-volume restore stage.

        Infra must create and containment-check the staging directory. This
        method preserves demo mode, bind policy, timeout, timezone and the
        fixed backup root; it does not create the directory or acquire locks.
        """
        staged_root = data_root.resolve(strict=False)
        if staged_root.drive.casefold() != self.data_root.drive.casefold():
            raise ConfigurationError(
                ConfigurationErrorCode.INVALID_DATA_ROOT,
                "staged_data_root",
                "restore staging must remain on the configured data volume",
            )
        return replace(self, data_root=staged_root)

    @classmethod
    def for_test(cls, data_root: Path, *, demo_mode: bool = False) -> "Settings":
        """Create isolated settings for tests; never selects a network bind."""
        return cls(data_root=data_root.resolve(strict=False), demo_mode=demo_mode)


def _local_app_data_root(environ: Mapping[str, str] | None = None) -> Path:
    source = os.environ if environ is None else environ
    configured = source.get("LOCALAPPDATA")
    if configured:
        return Path(configured)
    return Path.home() / "AppData" / "Local"


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Load and validate supported environment variables without reading secrets."""
    env = os.environ if environ is None else environ
    unknown = sorted(key for key in env if key.startswith("BENCHFLOW_") and key not in _ALLOWED_ENV)
    if unknown:
        raise ConfigurationError(
            ConfigurationErrorCode.INVALID_SETTING, unknown[0], "unsupported configuration variable"
        )

    host = env.get("BENCHFLOW_HOST", CANONICAL_HOST)
    port_text = env.get("BENCHFLOW_PORT", str(CANONICAL_PORT))
    if not port_text.isascii() or not port_text.isdecimal():
        raise ConfigurationError(
            ConfigurationErrorCode.INVALID_SETTING, "BENCHFLOW_PORT", "must be the fixed port 8000"
        )
    port = int(port_text)
    if host != CANONICAL_HOST or port != CANONICAL_PORT:
        raise ConfigurationError(
            ConfigurationErrorCode.UNSAFE_BIND,
            "BENCHFLOW_HOST/BENCHFLOW_PORT",
            "only 127.0.0.1:8000 is supported; no remote bind or port fallback",
        )

    demo_value = env.get("BENCHFLOW_DEMO", "0")
    if demo_value not in {"0", "1"}:
        raise ConfigurationError(
            ConfigurationErrorCode.INVALID_SETTING, "BENCHFLOW_DEMO", "must be exactly 0 or 1"
        )
    configured_root = env.get("BENCHFLOW_DATA_ROOT")
    if configured_root is None:
        data_folder = "demo" if demo_value == "1" else "data"
        data_root = _local_app_data_root(env) / "BenchFlow" / data_folder
    else:
        data_root = Path(configured_root)
        resolved_demo = data_root.resolve(strict=False)
        normal_data = (_local_app_data_root(env) / "BenchFlow" / "data").resolve(strict=False)
        if demo_value == "1" and resolved_demo == normal_data:
            raise ConfigurationError(
                ConfigurationErrorCode.UNSUPPORTED_DEMO_MODE,
                "BENCHFLOW_DATA_ROOT",
                "demo mode must use a separate isolated data root",
            )
    return Settings(
        data_root=data_root,
        local_app_data_root=_local_app_data_root(env),
        demo_mode=(demo_value == "1"),
        host=host,
        port=port,
    )
