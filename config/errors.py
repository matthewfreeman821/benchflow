"""Stable, secret-free configuration failures."""

from enum import StrEnum


class ConfigurationErrorCode(StrEnum):
    INVALID_SETTING = "INVALID_SETTING"
    UNSAFE_BIND = "UNSAFE_BIND"
    INVALID_DATA_ROOT = "INVALID_DATA_ROOT"
    UNSUPPORTED_DEMO_MODE = "UNSUPPORTED_DEMO_MODE"


class ConfigurationError(ValueError):
    def __init__(self, code: ConfigurationErrorCode, setting: str, message: str) -> None:
        self.code = code
        self.setting = setting
        self.message = message
        super().__init__(f"{setting}: {message}")
