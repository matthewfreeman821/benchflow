"""Same-origin local HTTP boundary and exclusive SQLite lifespan."""
from __future__ import annotations

from contextlib import asynccontextmanager, closing
from pathlib import Path
import secrets
import sqlite3
import threading
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import ValidationError
from starlette.exceptions import HTTPException

from config.settings import Settings, load_settings
from contracts.actions import parse_command
from contracts.auth import ActivateRequest, LoginRequest, LogoutRequest, RecoverRequest
from contracts.projections import BackupCreateRequest, BackupCreateResponse, BackupDetailView, BackupManifest
from contracts.errors import APIError, ErrorCode
from contracts.serialization import JSONBodyTooLargeError, loads_request_json
from backend import backups, commands, identity, queries, views
from backend.core import Rejected, event, insert, one, require, uid
from backend.database import (
    StorageError, _assert_local_plain_path, acquire_data_root_lock, connect,
    immediate_transaction, initialize_database, utc_now,
)

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


def cookie(response, token=None):
    if token:
        response.set_cookie("bf_session", token, httponly=True, samesite="lax", path="/", secure=False)
    else:
        response.delete_cookie("bf_session", httponly=True, samesite="lax", path="/", secure=False)
    return response


def failure(request, code, *, retryable=False, message=None):
    messages = {
        "AUTH_INVALID": "Sign-in could not be completed. Check details or contact the shop.",
        "AUTH_REQUIRED": "Sign in again or contact the shop.",
        "AUTH_CODE_INVALID": "This code could not be redeemed. Contact the shop.",
        "DEMO_CREDENTIALS_UNAVAILABLE": "Demo credentials are unavailable. Restart isolated demo mode to rotate credentials; fictional story records are preserved.",
    }
    error = APIError(ErrorCode(code), message or messages.get(str(code), "The request could not be completed."),
                     request.state.request_id, retryable=retryable)
    response = JSONResponse(error.envelope.model_dump(mode="json"), status_code=error.status_code)
    if code == "RATE_LIMITED":
        response.headers["Retry-After"] = "900"
    return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app):
        app.state.ready = False
        with acquire_data_root_lock(settings):
            status = initialize_database(settings)
            with closing(connect(settings)) as db, immediate_transaction(db):
                if not one(db, "SELECT 1 FROM shop_policy", required=False):
                    insert(db, "shop_policy", id=uid(), shop_timezone=settings.shop_timezone,
                           estimate_window_days=7, diagnostic_fee_minor=0, currency="USD",
                           tax_enabled=0, tax_label="Tax not configured")
            if settings.demo_mode:
                from backend.testing import rotate_demo
                app.state.demo_credentials = rotate_demo(settings)
            app.state.schema_version = status.schema_version
            app.state.ready = True
            try:
                yield
            finally:
                app.state.ready = False
                app.state.demo_credentials = None

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ready = False
    app.state.settings = settings
    app.state.clock = utc_now
    app.state.cursor_secret = secrets.token_bytes(32)
    app.state.demo_credentials = None
    app.state.demo_claim_lock = threading.Lock()
    app.state.backup_lock = threading.Lock()

    @app.middleware("http")
    async def boundary(request, call_next):
        request_id = request.headers.get("x-request-id", "")
        try:
            request_id = str(UUID(request_id))
        except (ValueError, AttributeError):
            request_id = str(uuid4())
        request.state.request_id = request_id
        try:
            require(request.headers.get("host") == "127.0.0.1:8000", "ORIGIN_INVALID")
            origin = request.headers.get("origin")
            require(origin is None or origin == settings.origin, "ORIGIN_INVALID")
            require(app.state.ready, "STORAGE_UNAVAILABLE")
            if request.method not in {"GET", "HEAD"}:
                require(origin == settings.origin, "ORIGIN_INVALID")
                require(request.headers.get("content-type", "").lower() in {
                    "application/json", "application/json; charset=utf-8"}, "CONTENT_TYPE_REQUIRED")
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    require(len(body) <= settings.max_json_body_bytes, "BODY_TOO_LARGE")
                try:
                    request.state.payload = loads_request_json(bytes(body), max_bytes=settings.max_json_body_bytes)
                except (ValueError, UnicodeError):
                    raise Rejected("INVALID_REQUEST") from None
            response = await call_next(request)
        except Rejected as error:
            response = failure(request, error.code, message=error.public_message)
            if error.code in {ErrorCode.AUTH_REQUIRED, ErrorCode.FORBIDDEN}:
                # A roles_changed stale cookie produces one 403; subsequent use is 401.
                token = request.cookies.get("bf_session")
                if token:
                    with closing(connect(settings)) as db, immediate_transaction(db):
                        stored = one(db, "SELECT revocation_reason FROM sessions WHERE token_hash=?",
                                     (identity.fingerprint(token),), required=False)
                        expire = error.code == ErrorCode.AUTH_REQUIRED or (
                            stored and stored["revocation_reason"] == "roles_changed")
                        if expire:
                            db.execute("DELETE FROM sessions WHERE token_hash=?", (identity.fingerprint(token),))
                            cookie(response)
        except (ValidationError, ValueError):
            response = failure(request, "VALIDATION_FAILED")
        except sqlite3.OperationalError as error:
            code = "DATABASE_BUSY" if getattr(error, "sqlite_errorcode", None) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} else "STORAGE_UNAVAILABLE"
            response = failure(request, code, retryable=code == "DATABASE_BUSY")
        except sqlite3.IntegrityError:
            response = failure(request, "VALIDATION_FAILED")
        except StorageError as error:
            response = failure(request, "INTERNAL_ERROR" if error.code == "COMMIT_OUTCOME_UNKNOWN" else "STORAGE_UNAVAILABLE")
        except Exception:
            # No request/body/SQL/credential/stack logging at the web boundary.
            response = failure(request, "INTERNAL_ERROR")
        response.headers.update({
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
            "X-Request-ID": request_id, "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        })
        return response

    @app.exception_handler(HTTPException)
    async def not_found(request, error):
        return failure(request, "NOT_FOUND" if error.status_code in {404, 405} else "INVALID_REQUEST")

    def validate(db, request, *, authenticated=True):
        csrf = request.headers.get("x-csrf-token", "") if request.method != "GET" else None
        return identity.validate_session(db, request.cookies.get("bf_session"), app.state.clock(),
                                         csrf=csrf, authenticated=authenticated)

    @app.get("/api/v1/health")
    def health():
        return dict(status="ready", schema_version=app.state.schema_version)

    @app.get("/api/v1/session")
    def session(request: Request):
        require(not request.query_params, "INVALID_REQUEST")
        now = app.state.clock()
        with closing(connect(settings)) as db, immediate_transaction(db):
            token = request.cookies.get("bf_session")
            try:
                actor, record = validate(db, request, authenticated=False)
            except Rejected as error:
                if error.code == ErrorCode.FORBIDDEN:
                    raise
                if token:
                    db.execute("DELETE FROM sessions WHERE token_hash=?", (identity.fingerprint(token),))
                token, csrf = identity.issue_session(db, now)
                return cookie(JSONResponse(dict(authenticated=False, csrf_token=csrf, account=None,
                                                demo_mode=settings.demo_mode)), token)
            csrf = identity.csrf_for(token)
            if actor:
                response = dict(authenticated=True, csrf_token=csrf, account=views.account(db, actor["id"], now),
                                idle_expires_at=record["idle_expires_at"], absolute_expires_at=record["absolute_expires_at"],
                                demo_mode=settings.demo_mode)
            else:
                response = dict(authenticated=False, csrf_token=csrf, account=None, demo_mode=settings.demo_mode)
            return JSONResponse(response)

    @app.post("/api/v1/auth/{operation}")
    def auth(operation: str, request: Request):
        require(not request.query_params, "INVALID_REQUEST")
        model = {"login": LoginRequest, "activate": ActivateRequest, "recover": RecoverRequest, "logout": LogoutRequest}.get(operation)
        require(model is not None, "NOT_FOUND")
        data = model.model_validate(request.state.payload)
        now, peer = app.state.clock(), request.client.host if request.client else "unknown"
        with closing(connect(settings)) as db, immediate_transaction(db):
            # Absent logout is idempotent, but still requires Origin and a valid pre-auth CSRF if a cookie is present.
            if operation == "logout" and not request.cookies.get("bf_session"):
                return cookie(Response(status_code=204))
            if operation == "logout":
                old = one(db, "SELECT * FROM sessions WHERE token_hash=?",
                          (identity.fingerprint(request.cookies.get("bf_session", "")),), required=False)
                if old is None:
                    return cookie(Response(status_code=204))
                from backend.core import instant
                if instant(now) >= instant(old["idle_expires_at"]) or instant(now) >= instant(old["absolute_expires_at"]):
                    import hmac
                    require(hmac.compare_digest(old["csrf_hash"], identity.fingerprint(request.headers.get("x-csrf-token", ""))), "CSRF_INVALID")
                    db.execute("DELETE FROM sessions WHERE token_hash=?", (old["token_hash"],))
                    return cookie(Response(status_code=204))
            actor, record = validate(db, request, authenticated=False)
            if operation == "login":
                require(actor is None, "FORBIDDEN")
                actor = identity.authenticate(db, data, peer, now)
                if actor is None:
                    return failure(request, "AUTH_INVALID")  # commit throttle/audit only
                db.execute("DELETE FROM sessions WHERE token_hash=?", (record["token_hash"],))
                token, csrf = identity.issue_session(db, now, actor)
                _, record = identity.validate_session(db, token, now)
                return cookie(JSONResponse(dict(authenticated=True, account=views.account(db, actor["id"], now), csrf_token=csrf,
                    idle_expires_at=record["idle_expires_at"], absolute_expires_at=record["absolute_expires_at"],
                    demo_mode=settings.demo_mode)), token)
            if operation == "logout":
                db.execute("DELETE FROM sessions WHERE token_hash=?", (record["token_hash"],))
                if actor:
                    event(db, actor, "account", actor["id"], "auth.logout", now)
                return cookie(Response(status_code=204))
            require(actor is None, "FORBIDDEN")
            redeemed = identity.redeem(db, data, operation, peer, now)
            if not redeemed:
                return failure(request, "AUTH_CODE_INVALID")
            db.execute("DELETE FROM sessions WHERE token_hash=?", (record["token_hash"],))
            return cookie(JSONResponse(dict(activated=True, login_id=data.login_id) if operation == "activate" else
                                       dict(recovered=True, sign_in_required=True), status_code=201 if operation == "activate" else 200))

    @app.post("/api/v1/commands")
    def command(request: Request):
        require(not request.query_params, "INVALID_REQUEST")
        parsed = parse_command(request.state.payload)
        with closing(connect(settings)) as db, immediate_transaction(db):
            actor, _ = validate(db, request)
            return commands.execute(db, parsed, actor, app.state.clock(), demo_mode=settings.demo_mode)

    @app.post("/api/v1/demo/credentials")
    def demo_credentials(request: Request):
        require(request.state.payload == {} and not request.query_params, "INVALID_REQUEST")
        require(settings.demo_mode, "DEMO_CREDENTIALS_UNAVAILABLE")
        require(request.client and request.client.host in {"127.0.0.1", "::1"}, "DEMO_CREDENTIALS_UNAVAILABLE")
        with closing(connect(settings)) as db, immediate_transaction(db):
            actor, _ = validate(db, request, authenticated=False)
            require(actor is None, "FORBIDDEN")
        with app.state.demo_claim_lock:
            payload = app.state.demo_credentials
            require(payload is not None, "DEMO_CREDENTIALS_UNAVAILABLE")
            app.state.demo_credentials = None
        return JSONResponse(payload)

    @app.get("/api/v1/{route:path}")
    def query(route: str, request: Request):
        require(len(request.query_params.multi_items()) == len(request.query_params), "INVALID_REQUEST")
        with closing(connect(settings)) as db, immediate_transaction(db):
            actor, _ = validate(db, request)
            if route == "staff/backups" or route.startswith("staff/backups/"):
                queries.staff(actor, {"manager"})
                if route == "staff/backups":
                    require(set(request.query_params) <= {"cursor", "limit"}, "INVALID_REQUEST")
                    items = [backups.metadata(BackupManifest.model_validate_json(r["manifest_json"]))
                             for r in db.execute("SELECT manifest_json FROM backup_operations WHERE state='complete' ORDER BY created_at,command_id")]
                    return queries.page(items, actor, route, dict(request.query_params), app.state.cursor_secret)
                require(not request.query_params, "INVALID_REQUEST")
                pieces = route.split("/")
                require(len(pieces) in {3, 4}, "BACKUP_NOT_FOUND")
                manifest = backups.verify_bundle(settings, pieces[2])
                if len(pieces) == 3:
                    return BackupDetailView(manifest=manifest, integrity="verified_now").model_dump(mode="json")
                if pieces[3] == "restore-guide":
                    return backups.restore_guide(manifest)
                require(pieces[3] == "download", "BACKUP_NOT_FOUND")
                return FileResponse(backups.bundle_path(settings, pieces[2]) / "benchflow.sqlite3",
                    media_type="application/octet-stream", filename="benchflow.sqlite3")
            return queries.read(db, actor, route, dict(request.query_params), app.state.clock(), app.state.cursor_secret)

    @app.post("/api/v1/staff/backups")
    def backup_create(request: Request):
        require(not request.query_params, "INVALID_REQUEST")
        data = BackupCreateRequest.model_validate(request.state.payload)
        with app.state.backup_lock:
            with closing(connect(settings)) as db, immediate_transaction(db):
                actor, _ = validate(db, request)
                queries.staff(actor, {"manager"})
            result = backups.create_for_actor(settings, actor, data.command_id, app.state.clock())
            return BackupCreateResponse.model_validate(result).model_dump(mode="json")

    @app.get("/{path:path}")
    def static(path: str):
        path = path or "index.html"
        require(path != "demo.html" or settings.demo_mode, "NOT_FOUND")
        target = FRONTEND / path
        require(".." not in Path(path).parts and "\\" not in path and ":" not in path, "NOT_FOUND")
        _assert_local_plain_path(target)
        target = target.resolve()
        require(target.is_relative_to(FRONTEND.resolve()) and target.suffix in {".html", ".css", ".js", ".svg", ".png", ".ico"}
                and target.is_file(), "NOT_FOUND")
        return FileResponse(target)

    return app
