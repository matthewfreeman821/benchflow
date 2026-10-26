"""Fail-closed Windows host operations. Never import this from the web app.

Backend schema/authentication hooks are deliberately required, not approximated.
All command diagnostics are closed codes; underlying exceptions stay private.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, closing
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
import uuid
from dataclasses import replace

from config.settings import load_settings

PROJECT = Path(__file__).resolve().parent.parent
ORIGIN = "http://127.0.0.1:8000"


class Refused(Exception):
    pass


def require(condition, code):
    if not condition:
        raise Refused(code)


def no_reparse(path: Path):
    """Check lexical path before resolution; reject junctions/symlinks in ancestors."""
    require(path.is_absolute() and not str(path).startswith(("\\\\", "//")), "LOCAL_PATH_REQUIRED")
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            st = part.lstat()
            require(not (getattr(st, "st_file_attributes", 0) & 0x400)
                    and not part.is_symlink(), "REPARSE_PATH_REFUSED")


def private_directory(path: Path):
    no_reparse(path)
    path.mkdir(parents=True, exist_ok=True)
    no_reparse(path)
    require(os.name == "nt", "WINDOWS_REQUIRED")
    # Replace inherited grants with a protected current-user-only DACL, rather
    # than merely adding a grant to an unknown preexisting directory.
    script = (
        "$p=$args[0]; $i=[Security.Principal.WindowsIdentity]::GetCurrent(); "
        "$a=New-Object Security.AccessControl.DirectorySecurity; "
        "$a.SetOwner($i.User); $a.SetAccessRuleProtection($true,$false); "
        "$r=New-Object Security.AccessControl.FileSystemAccessRule("
        "$i.User,'FullControl','ContainerInherit,ObjectInherit','None','Allow'); "
        "$a.AddAccessRule($r); Set-Acl -LiteralPath $p -AclObject $a"
    )
    # Argument is passed separately: no path interpolation into PowerShell code.
    command = "& { " + script + " }"
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command, str(path)],
        capture_output=True,
    )
    require(result.returncode == 0, "PRIVATE_ACL_FAILED")
    assert_private(path)


def assert_private(path: Path):
    """Reject backup/candidate grants to other principals; do not rewrite files."""
    no_reparse(path)
    script = (
        "$p=$args[0]; $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User; "
        "$a=Get-Acl -LiteralPath $p; "
        "if($a.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid.Value){exit 2}; "
        "foreach($r in $a.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])){"
        "if($r.AccessControlType -eq 'Allow' -and $r.IdentityReference.Value -ne $sid.Value){exit 3}}"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         "& { " + script + " }", str(path)], capture_output=True)
    require(result.returncode == 0, "PRIVATE_ACL_REQUIRED")


@contextmanager
def launcher_lock(root: Path):
    import msvcrt
    no_reparse(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".operations.lock"
    no_reparse(path)
    with path.open("a+b") as f:
        if f.tell() == 0:
            f.write(b"0")
            f.flush()
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise Refused("DATA_ROOT_BUSY") from None
        try:
            yield
        finally:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def settings_for(demo=False):
    # Refuse overrides that might accidentally target a user's normal data.
    env = dict(os.environ)
    require(env.get("BENCHFLOW_HOST", "127.0.0.1") == "127.0.0.1"
            and env.get("BENCHFLOW_PORT", "8000") == "8000", "UNSAFE_BIND")
    env["BENCHFLOW_DEMO"] = "1" if demo else "0"
    expected = Path(env["LOCALAPPDATA"]) / "BenchFlow" / ("demo" if demo else "data")
    if env.get("BENCHFLOW_DATA_ROOT"):
        require(Path(env["BENCHFLOW_DATA_ROOT"]).absolute() == expected.absolute(),
                "OPERATIONS_FIXED_ROOT_REQUIRED")
    env["BENCHFLOW_DATA_ROOT"] = str(expected)
    no_reparse(expected)
    settings = load_settings(env)
    # Pass only the same validated values to the app; no secret env examples.
    return settings, env


def backend_database():
    try:
        return importlib.import_module("backend.database")
    except ImportError:
        raise Refused("BACKEND_DATABASE_MISSING") from None


def inspect_database(settings):
    # Shared public Backend contract; no private migration implementation
    # or shadow ledger/authentication logic in Infra.
    hook = getattr(backend_database(), "inspect_database", None)
    require(callable(hook), "BACKEND_SCHEMA_INSPECTION_MISSING")
    return hook(settings)


@contextmanager
def offline(settings):
    private_directory(settings.data_root)
    with launcher_lock(settings.data_root):
        with backend_database().acquire_data_root_lock(settings):
            yield


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def health():
    # Ignore proxy environment and refuse redirects (even same-origin ones).
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(ORIGIN + "/api/v1/health",
                                     headers={"Accept": "application/json"})
    with opener.open(request, timeout=2) as response:
        require(response.status == 200, "NOT_READY")
        raw = response.read(4097)
        require(len(raw) <= 4096, "INVALID_HEALTH")
        payload = json.loads(raw)
    require(isinstance(payload, dict) and set(payload) == {"status", "schema_version"}
            and payload["status"] == "ready"
            and type(payload["schema_version"]) is int
            and payload["schema_version"] >= 1, "INVALID_HEALTH")
    return payload


def assert_port_free():
    with socket.socket() as sock:
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind(("127.0.0.1", 8000))
        except OSError:
            raise Refused("PORT_8000_OCCUPIED") from None


def process_identity(pid):
    """Creation time + image identify an exact Windows process, not PID alone."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    handle = kernel.OpenProcess(0x1000, False, pid)
    require(bool(handle), "RECORDED_PROCESS_UNAVAILABLE")
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        require(kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)),
                "PROCESS_IDENTITY_FAILED")
        size = wintypes.DWORD(32768)
        image = ctypes.create_unicode_buffer(size.value)
        require(kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)),
                "PROCESS_IDENTITY_FAILED")
        created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return {"pid": pid, "created": created, "image": image.value}
    finally:
        kernel.CloseHandle(handle)


def start(settings, env):
    require((PROJECT / "backend" / "app.py").is_file(), "BACKEND_APP_MISSING")
    backend_database()  # Missing interface fails before any listener.
    private_directory(settings.data_root)
    state = settings.data_root / ".operations.json"
    stop = settings.data_root / ".operations-stop"
    with launcher_lock(settings.data_root):
        # Stale record may refer to a running process. Never overwrite it.
        require(not state.exists(), "STALE_STATE_REVIEW_REQUIRED")
        assert_port_free()
        stop.unlink(missing_ok=True)
        command = [sys.executable, "-m", "uvicorn", "backend.app:create_app", "--factory",
                   "--host", "127.0.0.1", "--port", "8000", "--workers", "1",
                   "--no-access-log", "--log-level", "critical", "--no-proxy-headers"]
        child = subprocess.Popen(command, cwd=PROJECT, env=env,
                                 creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            identity = {"child": process_identity(child.pid),
                        "supervisor": process_identity(os.getpid())}
            state.write_text(json.dumps(identity), encoding="utf-8")
            deadline = time.monotonic() + 20
            while True:
                require(child.poll() is None, "APP_START_FAILED")
                try:
                    health()
                    break
                except Exception:
                    require(time.monotonic() < deadline, "READINESS_TIMEOUT")
                    time.sleep(.2)
            print("READY origin=127.0.0.1:8000 workers=1", flush=True)
            while child.poll() is None and not stop.exists():
                time.sleep(.2)
        except KeyboardInterrupt:
            pass
        finally:
            if child.poll() is None:
                try:
                    child.send_signal(signal.CTRL_BREAK_EVENT)
                    child.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    # Popen owns the original process handle, safe against PID reuse.
                    child.terminate()
                    child.wait(timeout=10)
            state.unlink(missing_ok=True)
            stop.unlink(missing_ok=True)
        print("STOPPED", flush=True)


def stop(settings):
    state = settings.data_root / ".operations.json"
    no_reparse(state)
    require(state.is_file(), "NO_OWNED_PROCESS_RECORD")
    recorded = json.loads(state.read_text(encoding="utf-8"))
    require(set(recorded) == {"child", "supervisor"}, "STALE_STATE_REVIEW_REQUIRED")
    for role in ("child", "supervisor"):
        require(process_identity(recorded[role]["pid"]) == recorded[role],
                "PID_IDENTITY_MISMATCH")
    stop_file = settings.data_root / ".operations-stop"
    no_reparse(stop_file)
    stop_file.touch()
    deadline = time.monotonic() + 15
    while state.exists() and time.monotonic() < deadline:
        time.sleep(.2)
    require(not state.exists(), "SUPERVISOR_NOT_RESPONDING_NO_PROCESS_KILLED")
    print("STOPPED")


def integrity(path):
    no_reparse(path)
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as db:
        require(db.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
                "SQLITE_INTEGRITY_FAILED")
        require(not db.execute("PRAGMA foreign_key_check").fetchall(), "FOREIGN_KEYS_FAILED")


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def provenance():
    return importlib.import_module("backend.operator").current_operator_provenance()


def checkpoint(path):
    if not path.exists():
        return
    no_reparse(path)
    # Closing explicitly matters on Windows; `with connection` only commits.
    with closing(sqlite3.connect(path)) as db:
        result = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        require(result is not None and result[0] == 0, "CHECKPOINT_BUSY")
    require(not Path(str(path) + "-wal").exists()
            and not Path(str(path) + "-shm").exists()
            and not Path(str(path) + "-journal").exists(), "SIDECARS_REMAIN")


def current_schema(status):
    return (status.schema_version >= 1
            and status.schema_version == status.latest_schema_version
            and not status.pending_migration_versions
            and status.integrity_check == "ok" and status.foreign_key_check == "ok")


def snapshot(settings):
    """Offline consistent snapshot; caller holds both locks."""
    require(settings.database_path.is_file(), "DATABASE_MISSING")
    no_reparse(settings.database_path)
    status = inspect_database(settings)
    require(current_schema(status), "SCHEMA_NOT_READY")
    private_directory(settings.backup_root)
    backup_id = str(uuid.uuid4())
    # Backend owns SQLite online backup, strict manifest and atomic publication.
    # Inherit the private backup-root ACL; never pre-create its final directory.
    importlib.import_module("backend.backups").create_consistent_snapshot(
        settings, backup_id=backup_id,
        created_at=datetime.now(timezone.utc).isoformat())
    validate_bundle(settings, backup_id)
    return backup_id


def validate_bundle(settings, backup_id):
    from contracts.projections import BackupManifest
    from contracts.serialization import loads_request_json

    require(str(uuid.UUID(backup_id)) == backup_id, "CANONICAL_BACKUP_UUID_REQUIRED")
    bundle = settings.backup_root / backup_id
    no_reparse(bundle)
    manifest_path = bundle / "manifest.json"
    data = bundle / "benchflow.sqlite3"
    no_reparse(manifest_path)
    no_reparse(data)
    for path in (bundle, manifest_path, data):
        assert_private(path)
    require(manifest_path.stat().st_size <= 8192, "INVALID_MANIFEST")
    try:
        manifest = BackupManifest.model_validate(
            loads_request_json(manifest_path.read_bytes(), max_bytes=8192))
    except ValueError:
        raise Refused("INVALID_MANIFEST") from None
    require(manifest.backup_id == backup_id
            and manifest.size_bytes == data.stat().st_size
            and manifest.sha256 == sha256(data), "BACKUP_MANIFEST_MISMATCH")
    # Positive supported prefixes are valid restore sources, not new snapshots.
    with backend_database().acquire_data_root_lock(settings.for_staged_root(bundle)):
        checked = inspect_database(settings.for_staged_root(bundle))
        require(checked.schema_version == manifest.schema_version, "BACKUP_SCHEMA_MISMATCH")
    return data, manifest


def restore(settings, backup_id, confirmation):
    require(confirmation == f"RESTORE BENCHFLOW {backup_id}", "CONFIRMATION_REQUIRED")
    hooks = importlib.import_module("backend.recovery")
    require(callable(getattr(hooks, "invalidate_restored_authentication", None))
            and callable(getattr(backend_database(), "inspect_database", None)),
            "RESTORE_HOOK_MISSING")
    with offline(settings):
        source, manifest = validate_bundle(settings, backup_id)
        # Separate same-volume data root: Backend's helper acquires its OWN
        # root lock and initializes/migrates only this candidate.
        stage_root = settings.data_root / f".restore-{uuid.uuid4()}"
        private_directory(stage_root)
        staged = replace(settings, data_root=stage_root)
        stage = staged.database_path
        try:
            with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as src:
                with closing(sqlite3.connect(stage)) as dst:
                    src.backup(dst)
            with backend_database().acquire_data_root_lock(staged):
                before = inspect_database(staged)
                require(before.schema_version == manifest.schema_version,
                        "BACKUP_SCHEMA_MISMATCH")
            # Release candidate lock BEFORE this self-locking Backend helper.
            hooks.invalidate_restored_authentication(
                staged, source_backup_id=backup_id, operator_provenance=provenance())
            # Reacquire and reinspect; keep both candidate and live locks
            # through preservation and same-volume replacement.
            with backend_database().acquire_data_root_lock(staged):
                checkpoint(stage)
                checked = inspect_database(staged)
                require(current_schema(checked), "STAGED_SCHEMA_NOT_READY")
                if settings.database_path.exists():
                    prior = snapshot(settings)
                    print(f"PRE_RESTORE_BACKUP {prior}")
                    checkpoint(settings.database_path)
                else:
                    require(not any(Path(str(settings.database_path) + s).exists()
                                    for s in ("-wal", "-shm", "-journal")), "SIDECARS_REMAIN")
                no_reparse(settings.database_path)
                os.replace(stage, settings.database_path)
                print("RESTORED_STOPPED_MANAGER_RECOVERY_REQUIRED")
        finally:
            # Preserve failed staging files for private diagnosis; never purge
            # unknown sidecars or user data, including on an interrupted restore.
            pass


def operator(args, settings):
    if args.command == "restore":
        restore(settings, args.backup_id, args.confirm)
        return
    if args.command == "backup":
        with offline(settings):
            print("BACKUP " + snapshot(settings))
        return
    if args.command == "demo-reset":
        expected = settings.local_app_data_root / "BenchFlow" / "demo"
        require(settings.demo_mode and settings.data_root == expected
                and args.confirm == "RESET FICTIONAL BENCHFLOW DEMO", "DEMO_RESET_REFUSED")
        with offline(settings):
            prior = snapshot(settings)
            checkpoint(settings.database_path)
            destination = settings.data_root / f"preserved-reset-{uuid.uuid4()}.sqlite3"
            settings.database_path.rename(destination)
            print("DEMO_RESET_PRESERVED " + prior)
        return
    require(not settings.demo_mode, "USE_DEMO_START_ROTATION")
    # Backend's real CLI owns verification/no-echo prompts and self-locking.
    require(sys.stdin.isatty(), "INTERACTIVE_NO_ECHO_TERMINAL_REQUIRED")
    if args.command == "bootstrap":
        require(bool(args.login_id) and bool(args.display_name), "BOOTSTRAP_INPUT_REQUIRED")
        command = ["bootstrap-manager", "--login-id", args.login_id,
                   "--display-name", args.display_name]
    else:
        require(bool(args.account_id) and args.verification_method in {"in_person", "phone"},
                "RECOVERY_INPUT_REQUIRED")
        command = ["recover-manager", "--account-id", args.account_id,
                   "--verification-method", args.verification_method]
    private_directory(settings.data_root)
    with launcher_lock(settings.data_root):  # NOT offline(): CLI self-locks
        _, env = settings_for(False)
        result = subprocess.run([sys.executable, "-m", "backend.cli", *command],
                                cwd=PROJECT, env=env)
        require(result.returncode == 0, "OPERATOR_ACTION_FAILED")
    print("OPERATOR_ACTION_COMPLETE")


def main():
    parser = argparse.ArgumentParser(description="BenchFlow local host operations")
    parser.add_argument("command", choices=["start", "stop", "health", "backup", "restore",
                                            "bootstrap", "recover", "demo-reset"])
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--backup-id")
    parser.add_argument("--confirm")
    parser.add_argument("--login-id")
    parser.add_argument("--display-name")
    parser.add_argument("--account-id")
    parser.add_argument("--verification-method", choices=["in_person", "phone"])
    args = parser.parse_args()
    try:
        require(os.name == "nt", "WINDOWS_REQUIRED")
        if args.command == "health":
            print(json.dumps(health()))
            return 0
        settings, env = settings_for(args.demo)
        if args.command == "start":
            start(settings, env)
        elif args.command == "stop":
            stop(settings)
        else:
            operator(args, settings)
        return 0
    except Refused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception:
        # No raw exception messages, traces, SQL, paths, business values or secrets.
        print("OPERATION_FAILED_REVIEW_PRIVATE_STATE", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
