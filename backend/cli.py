"""Offline CLI; passwords are holder-entered only, never command-line options."""

from __future__ import annotations

import argparse
from getpass import getpass
import sqlite3
import subprocess

from pydantic import ValidationError

from config.settings import load_settings
from backend.bootstrap import bootstrap_manager
from backend.database import StorageError
from backend.recovery import recover_manager


def _password() -> str:
    first = getpass("Enter your new password (12–256 characters): ")
    second = getpass("Confirm your new password: ")
    if first != second:
        raise ValueError("PASSWORD_CONFIRMATION_FAILED")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.cli", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap-manager", allow_abbrev=False)
    bootstrap.add_argument("--login-id", required=True)
    bootstrap.add_argument("--display-name", required=True)
    recovery = commands.add_parser("recover-manager", allow_abbrev=False)
    recovery.add_argument("--account-id", required=True)
    recovery.add_argument("--verification-method", choices=("in_person", "phone"), required=True)
    options = parser.parse_args(argv)
    try:
        settings = load_settings()
        if options.command == "bootstrap-manager":
            result = bootstrap_manager(
                settings, login_id=options.login_id, display_name=options.display_name, password=_password(),
            )
            print(f"Manager bootstrap: {result.state}; account ID {result.account_id}")
        else:
            print("Verify the manager in person or call an already recorded contact number.")
            print("OS operator provenance is recorded; this is not independent identity proof.")
            expected = f"RECOVER BENCHFLOW MANAGER {options.account_id}"
            if input(f"After verification, type {expected}: ") != expected:
                raise ValueError("VERIFICATION_NOT_CONFIRMED")
            recover_manager(
                settings, account_id=options.account_id, password=_password(),
                verification_method=options.verification_method,
            )
            print("Manager recovery committed. Sign in with the holder-entered password.")
        return 0
    except (ValueError, ValidationError, StorageError, sqlite3.Error, OSError, RuntimeError, subprocess.SubprocessError):
        # Never print exception text, input, SQL, hashes, or filesystem paths.
        print("Operation not completed. Check local configuration, identity verification, and storage availability.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
