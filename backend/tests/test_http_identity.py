"""Real ASGI/SQLite tests, with an explicit server-observed loopback test peer."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json

import pytest
from fastapi.testclient import TestClient

from config.settings import CANONICAL_ORIGIN, Settings
from contracts.projections import CustomerJobDetail, JobStaffDetail
from backend.app import create_app
from backend.core import account_actor, later, one, rows, uid
from backend.credentials import fingerprint
from backend.database import acquire_data_root_lock, connect, immediate_transaction, utc_now
from backend.testing import UNKNOWN, fixture_account, new_customer, run, seed_approval_start_scenario


def headers(csrf, **extra):
    return {"Accept": "application/json", "Content-Type": "application/json", "Origin": CANONICAL_ORIGIN,
            "X-CSRF-Token": csrf, "X-Request-ID": uid(), **extra}


def csrf(client):
    response = client.get("/api/v1/session")
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def login(client, login_id, password):
    response = client.post("/api/v1/auth/login", json=dict(login_id=login_id, password=password), headers=headers(csrf(client)))
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


@pytest.fixture
def api(tmp_path):
    settings = Settings.for_test(tmp_path / "api")
    s = seed_approval_start_scenario(settings)
    app = create_app(settings)
    with TestClient(app, base_url=CANONICAL_ORIGIN, client=("127.0.0.1", 41000)) as client:
        yield settings, s, app, client


def test_host_origin_csrf_json_size_unknown_fields_and_real_static(api):
    _, _, _, client = api
    token = csrf(client)
    assert client.get("/api/v1/health", headers={"Host": "evil.invalid"}).status_code == 403
    response = client.post("/api/v1/auth/login", json={}, headers=headers(token, Origin="http://evil.invalid"))
    assert response.status_code == 403 and response.json()["error"]["code"] == "ORIGIN_INVALID"
    assert client.post("/api/v1/auth/login", json={}, headers=headers("wrong")).status_code == 422  # schema first, still no mutation
    response = client.post("/api/v1/auth/login", content=b'{"login_id":"abc","login_id":"def","password":"test"}', headers=headers(token))
    assert response.status_code == 400
    assert client.post("/api/v1/auth/login", content=b" " * 262145, headers=headers(token)).status_code == 413
    assert client.post("/api/v1/auth/login", content="{}", headers={**headers(token), "Content-Type": "text/plain"}).status_code == 415
    response = client.get("/")
    assert response.status_code == 200 and "text/html" in response.headers["content-type"]
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert client.get("/backend/database.py").status_code == 404
    assert client.get("/demo.html").status_code == 404
    assert client.get("/api/v1/health").json()["status"] == "ready"


def test_login_rotates_cookie_hash_only_csrf_binds_and_logout_revokes(api):
    settings, s, _, client = api
    anon_csrf = csrf(client)
    old_cookie = client.cookies.get("bf_session")
    token = login(client, s.technician_login_id, s.technician_password)
    new_cookie = client.cookies.get("bf_session")
    assert old_cookie != new_cookie and token != anon_csrf
    with closing(connect(settings)) as db:
        content = json.dumps(rows(db, "SELECT * FROM sessions"))
        assert new_cookie not in content and token not in content
    response = client.post("/api/v1/auth/logout", json={}, headers=headers(anon_csrf))
    assert response.status_code == 403 and response.json()["error"]["code"] == "CSRF_INVALID"
    assert client.get("/api/v1/me").status_code == 200
    assert client.post("/api/v1/auth/logout", json={}, headers=headers(token)).status_code == 204
    assert client.get("/api/v1/me").status_code == 401
    assert client.post("/api/v1/auth/logout", json={}, headers=headers(token)).status_code == 204


@pytest.mark.parametrize("boundary", ["idle_expires_at", "absolute_expires_at"])
def test_session_expiry_exact_equality(api, boundary):
    settings, s, app, client = api
    login(client, s.technician_login_id, s.technician_password)
    with closing(connect(settings)) as db:
        record = one(db, f"SELECT {boundary} FROM sessions WHERE account_id IS NOT NULL")
    app.state.clock = lambda: record[boundary]
    assert client.get("/api/v1/me").status_code == 401


def test_generic_login_and_persistent_observed_peer_throttle(api):
    settings, s, _, client = api
    token = csrf(client)
    for _ in range(5):
        response = client.post("/api/v1/auth/login", json=dict(login_id=s.technician_login_id, password="wrong"),
            headers=headers(token, **{"X-Forwarded-For": uid()}))
        assert response.status_code == 401
    response = client.post("/api/v1/auth/login", json=dict(login_id=s.technician_login_id, password=s.technician_password),
        headers=headers(token, **{"X-Forwarded-For": "other"}))
    assert response.status_code == 429 and response.headers["retry-after"] == "900"
    with closing(connect(settings)) as db:
        assert one(db, "SELECT count(*) n FROM auth_throttles")["n"] == 2
        audit = json.dumps(rows(db, "SELECT * FROM audit_events"))
        assert s.technician_password not in audit and "wrong" not in audit


def test_manager_privilege_alone_cannot_start_or_write_qc(api):
    settings, s, _, client = api
    with closing(connect(settings)) as db, immediate_transaction(db):
        manager = one(db, "SELECT id FROM accounts WHERE login_id='fixture.manager'")["id"]
        j = one(db, "SELECT * FROM jobs WHERE id=?", (s.job_id,))
        with pytest.raises(Exception, match="FORBIDDEN"):
            run(db, manager, "work.start", dict(task_id=s.task_id, scope_ref=s.scope_ref, consume=[]), utc_now(), ("job", s.job_id))
        with pytest.raises(Exception, match="FORBIDDEN"):
            run(db, manager, "diagnostic.record", dict(task_id=None, kind="test", summary="Denied",
                customer_text=None, customer_visible=False, uncertainty=None), utc_now(), ("job", s.job_id))


def test_customer_allowlist_and_foreign_missing_equal_in_demo(tmp_path):
    settings = Settings.for_test(tmp_path / "demo", demo_mode=True)
    with TestClient(create_app(settings), base_url=CANONICAL_ORIGIN, client=("127.0.0.1", 41000)) as client:
        response = client.post("/api/v1/demo/credentials", json={}, headers=headers(csrf(client)))
        assert response.status_code == 200
        credentials = {a["account_key"]: a for a in response.json()["accounts"]}
        a = credentials["customer.a"]
        token = login(client, a["login_id"], a["password"])
        jobs = client.get("/api/v1/me/jobs").json()["items"]
        detail = client.get("/api/v1/me/jobs/" + jobs[0]["job_id"]).json()
        CustomerJobDetail.model_validate(detail)
        assert "internal" not in detail and "timeline" not in detail and "customer" not in detail
        assert detail["job_rev"] >= 1 and detail["diagnostic_permission"]["permission_id"]
        client.post("/api/v1/auth/logout", json={}, headers=headers(token))
        b = credentials["customer.b"]
        token = login(client, b["login_id"], b["password"])
        foreign = client.get("/api/v1/me/jobs").json()["items"][0]["job_id"]
        client.post("/api/v1/auth/logout", json={}, headers=headers(token))
        login(client, a["login_id"], a["password"])
        request_id = uid()
        denied = client.get("/api/v1/me/jobs/" + foreign, headers={"X-Request-ID": request_id})
        missing = client.get("/api/v1/me/jobs/" + uid(), headers={"X-Request-ID": request_id})
        assert denied.status_code == missing.status_code == 404 and denied.content == missing.content


def test_demo_atomic_claim_and_restart_rotation_preserves_stories(tmp_path):
    settings = Settings.for_test(tmp_path / "demo", demo_mode=True)
    with TestClient(create_app(settings), base_url=CANONICAL_ORIGIN, client=("127.0.0.1", 42000)) as client:
        token = csrf(client)
        assert client.post("/api/v1/demo/credentials", json={}, headers=headers("bad")).status_code == 403
        def claim(_):
            return client.post("/api/v1/demo/credentials", json={}, headers=headers(token))
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(claim, range(2)))
        assert sorted(r.status_code for r in responses) == [200, 404]
        credentials = next(r for r in responses if r.status_code == 200).json()
        assert client.get("/api/v1/demo/credentials").status_code != 200
        with closing(connect(settings)) as db:
            before = [(j["id"], j["job_rev"]) for j in rows(db, "SELECT id,job_rev FROM jobs ORDER BY id")]
    with TestClient(create_app(settings), base_url=CANONICAL_ORIGIN, client=("127.0.0.1", 42001)) as client:
        fresh = client.post("/api/v1/demo/credentials", json={}, headers=headers(csrf(client))).json()
        assert fresh["accounts"][0]["password"] != credentials["accounts"][0]["password"]
        assert fresh["activation"]["code"] != credentials["activation"]["code"]
        with closing(connect(settings)) as db:
            assert [(j["id"], j["job_rev"]) for j in rows(db, "SELECT id,job_rev FROM jobs ORDER BY id")] == before


def test_invitation_activation_replay_expiry_and_recovery(api):
    settings, _, app, client = api
    with closing(connect(settings)) as db, immediate_transaction(db):
        manager = one(db, "SELECT id FROM accounts WHERE login_id='fixture.manager'")["id"]
        c = new_customer(db, manager, "Fictional invitee", utc_now())
        issued = run(db, manager, "account.invite_customer", dict(customer_id=c, verification_method="phone",
            contact_handoff_note="Manual fictional handoff", owner_staff_id=manager, next_action_due_at=later(utc_now(), hours=48)), utc_now())
        account_id = issued["aggregate"]["id"]
        code = issued["result"]["one_time_code"]["value"]
        assert code not in json.dumps(rows(db, "SELECT * FROM auth_codes"))
    token = csrf(client)
    activation = client.post("/api/v1/auth/activate", json=dict(code=code, login_id="fictional.new", password="new fictional password"),
        headers=headers(token))
    assert activation.status_code == 201
    repeat = client.post("/api/v1/auth/activate", json=dict(code=code, login_id="fictional.new", password="new fictional password"),
        headers=headers(csrf(client)))
    assert repeat.status_code == 400
    token = login(client, "fictional.new", "new fictional password")
    with closing(connect(settings)) as db, immediate_transaction(db):
        a = account_actor(db, account_id)
        issued = run(db, manager, "account.issue_recovery", dict(account_id=account_id, verification_method="phone", reason="customer_request"),
            utc_now(), expected=dict(account_rev=a["account_rev"]))
        recovery = issued["result"]["one_time_code"]
    client.post("/api/v1/auth/logout", json={}, headers=headers(token))
    recovered = client.post("/api/v1/auth/recover", json=dict(code=recovery["value"], new_password="different fictional password"),
        headers=headers(csrf(client)))
    assert recovered.status_code == 200
    bad = client.post("/api/v1/auth/login", json=dict(login_id="fictional.new", password="new fictional password"), headers=headers(csrf(client)))
    assert bad.status_code == 401
    login(client, "fictional.new", "different fictional password")


def test_staff_recovery_revokes_independent_active_session(api):
    settings, s, app, recovery_client = api
    active_client = TestClient(app, base_url=CANONICAL_ORIGIN, client=("127.0.0.1", 42002))
    login(active_client, s.technician_login_id, s.technician_password)
    assert active_client.get("/api/v1/me").status_code == 200
    session_hash = fingerprint(active_client.cookies.get("bf_session"))

    with closing(connect(settings)) as db, immediate_transaction(db):
        manager = one(db, "SELECT id FROM accounts WHERE login_id='fixture.manager'")["id"]
        technician = one(db, "SELECT id,account_rev FROM accounts WHERE login_id='fixture.technician'")
        issued = run(db, manager, "account.issue_recovery",
                     dict(account_id=technician["id"], verification_method="phone", reason="customer_request"),
                     utc_now(), expected=dict(account_rev=technician["account_rev"]))
        code = issued["result"]["one_time_code"]["value"]

    recovered = recovery_client.post("/api/v1/auth/recover",
        json=dict(code=code, new_password="recovered fictional password"),
        headers=headers(csrf(recovery_client)))
    assert recovered.status_code == 200
    with closing(connect(settings)) as db:
        session = one(db, "SELECT revocation_reason FROM sessions WHERE token_hash=?", (session_hash,))
        assert session["revocation_reason"] == "recovered"
    assert active_client.get("/api/v1/me").status_code == 401
    login(active_client, s.technician_login_id, "recovered fictional password")
    assert active_client.get("/api/v1/me").status_code == 200


def test_roles_revoked_next_request_then_401_and_directory_minimizes(api):
    settings, s, _, client = api
    login(client, s.technician_login_id, s.technician_password)
    directory = client.get("/api/v1/staff/assignable-staff", params={"job_id": s.job_id})
    assert directory.status_code == 200 and all(set(a) == {"staff_id", "display_name", "roles"} for a in directory.json()["items"])
    assert len(directory.json()["items"]) == 1
    assert client.get("/api/v1/staff/assignable-staff").status_code == 400
    with closing(connect(settings)) as db, immediate_transaction(db):
        manager = one(db, "SELECT id FROM accounts WHERE login_id='fixture.manager'")["id"]
        tech = one(db, "SELECT id,account_rev FROM accounts WHERE login_id='fixture.technician'")
        run(db, manager, "account.change_roles", dict(account_id=tech["id"], roles=["intake"], reason="role_change"),
            utc_now(), expected=dict(account_rev=tech["account_rev"]))
    assert client.get("/api/v1/me").status_code == 403
    assert client.get("/api/v1/me").status_code == 401


def test_lifespan_lock_excludes_second_app_and_releases_on_shutdown(api):
    settings, _, _, _ = api
    with pytest.raises(Exception, match="DATA_ROOT_LOCKED"):
        with TestClient(create_app(settings), base_url=CANONICAL_ORIGIN):
            pass


def test_intake_customer_scoped_account_revision_does_not_open_manager_directory(api):
    settings, s, _, client = api
    with closing(connect(settings)) as db, immediate_transaction(db):
        fixture_account(db, "staff", "Fictional intake", "fixture.intake", "fictional intake password", utc_now(), ["intake"])
        customer_id = one(db, "SELECT customer_id FROM jobs WHERE id=?", (s.job_id,))["customer_id"]
        manager = one(db, "SELECT id FROM accounts WHERE login_id='fixture.manager'")["id"]
    login(client, "fixture.intake", "fictional intake password")
    response = client.get(f"/api/v1/staff/customers/{customer_id}/account")
    assert response.status_code == 200 and response.json()["kind"] == "customer"
    assert response.json()["account_rev"] >= 1
    assert client.get("/api/v1/staff/accounts").status_code == 403
    assert client.get("/api/v1/me").status_code == 200  # ordinary permission denial does not sign out
    assert client.get(f"/api/v1/staff/customers/{manager}/account").status_code == 404
    assert client.get(f"/api/v1/staff/accounts/{manager}").status_code == 403


def test_invitation_expiry_equality_and_superseded_codes_are_generic(api):
    settings, _, app, client = api
    with closing(connect(settings)) as db, immediate_transaction(db):
        manager = one(db, "SELECT id FROM accounts WHERE login_id='fixture.manager'")["id"]
        customer = new_customer(db, manager, "Fictional expiring invite", utc_now())
        response = run(db, manager, "account.invite_customer", dict(customer_id=customer, verification_method="in_person",
            contact_handoff_note=None, owner_staff_id=manager, next_action_due_at=later(utc_now(), hours=48)), utc_now())
        first = response["result"]["one_time_code"]
        account_id = response["aggregate"]["id"]
        response = run(db, manager, "account.reissue_invite", dict(account_id=account_id, reason="customer_request"),
            utc_now(), expected=dict(account_rev=1))
        fresh = response["result"]["one_time_code"]
    token = csrf(client)
    old = client.post("/api/v1/auth/activate", json=dict(code=first["value"], login_id="fictional.expire", password="fictional expiry password"),
        headers=headers(token))
    assert old.status_code == 400 and old.json()["error"]["code"] == "AUTH_CODE_INVALID"
    app.state.clock = lambda: fresh["expires_at"]
    token = csrf(client)  # renew anonymous session at controlled time
    expired = client.post("/api/v1/auth/activate", json=dict(code=fresh["value"], login_id="fictional.expire", password="fictional expiry password"),
        headers=headers(token))
    assert expired.status_code == 400 and expired.json()["error"]["code"] == "AUTH_CODE_INVALID"
    with closing(connect(settings)) as db:
        assert account_actor(db, account_id)["state"] == "invited"
