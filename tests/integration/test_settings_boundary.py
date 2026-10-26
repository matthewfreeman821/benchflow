from __future__ import annotations

from pathlib import Path

import pytest

from config.errors import ConfigurationError, ConfigurationErrorCode
from config.settings import (
    CANONICAL_HOST,
    CANONICAL_ORIGIN,
    CANONICAL_PORT,
    DEFAULT_SHOP_TIMEZONE,
    Settings,
    load_settings,
)


def test_settings_defaults_are_loopback_and_local(tmp_path: Path) -> None:
    settings = Settings.for_test(tmp_path / "local-data", demo_mode=False)
    assert settings.host == CANONICAL_HOST == "127.0.0.1"
    assert settings.port == CANONICAL_PORT == 8000
    assert settings.origin == CANONICAL_ORIGIN
    assert settings.shop_timezone == DEFAULT_SHOP_TIMEZONE
    assert settings.database_path == settings.data_root / "benchflow.sqlite3"
    assert not settings.data_root.exists()


def test_staged_settings_preserve_runtime_configuration_and_backup_root(tmp_path: Path) -> None:
    settings = Settings.for_test(tmp_path / "shop-data", demo_mode=True)
    staged = settings.for_staged_root(settings.data_root / ".restore-candidate")
    assert staged.data_root == (settings.data_root / ".restore-candidate").resolve()
    assert staged.demo_mode is True
    assert staged.local_app_data_root == settings.local_app_data_root
    assert staged.backup_root == settings.backup_root
    assert staged.origin == settings.origin
    assert staged.busy_timeout_ms == settings.busy_timeout_ms
    assert staged.shop_timezone == settings.shop_timezone
    assert not staged.data_root.exists()


@pytest.mark.parametrize(
    "environ",
    [
        {"BENCHFLOW_HOST": "0.0.0.0"},
        {"BENCHFLOW_HOST": "::"},
        {"BENCHFLOW_PORT": "8001"},
        {"BENCHFLOW_DEMO": "yes"},
        {"BENCHFLOW_UNKNOWN_SETTING": "value"},
    ],
)
def test_unsafe_or_unknown_runtime_settings_fail_closed(environ: dict[str, str]) -> None:
    with pytest.raises(ConfigurationError):
        load_settings(environ)


def test_demo_uses_distinct_root_from_non_demo_data(tmp_path: Path) -> None:
    local_app_data = tmp_path / "Local"
    settings = load_settings({"BENCHFLOW_DEMO": "1", "LOCALAPPDATA": str(local_app_data)})
    assert settings.demo_mode is True
    assert settings.data_root == (local_app_data / "BenchFlow" / "demo").resolve()
    assert settings.backup_root == (local_app_data / "BenchFlow" / "backups").resolve()
    with pytest.raises(ConfigurationError) as error:
        load_settings({
            "BENCHFLOW_DEMO": "1",
            "BENCHFLOW_DATA_ROOT": str(local_app_data / "BenchFlow" / "data"),
            "LOCALAPPDATA": str(local_app_data),
        })
    assert error.value.code == ConfigurationErrorCode.UNSUPPORTED_DEMO_MODE


def test_network_data_root_is_rejected() -> None:
    with pytest.raises(ConfigurationError):
        Settings(data_root=Path(r"\\server\share\benchflow"))
