"""Cross-account reads must be indistinguishable from nonexistent resources."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from config.settings import CANONICAL_ORIGIN, Settings
from backend.app import create_app


def _request_headers(*, csrf: str | None = None, request_id: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Origin": CANONICAL_ORIGIN,
        "X-Request-ID": request_id or str(uuid.uuid4()),
    }
    if csrf is not None:
        headers.update({"Content-Type": "application/json", "X-CSRF-Token": csrf})
    return headers


def _csrf(client: TestClient) -> str:
    response = client.get("/api/v1/session", headers={"Accept": "application/json"})
    assert response.status_code == 200
    assert response.json()["demo_mode"] is True
    return response.json()["csrf_token"]


def _login(client: TestClient, login_id: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"login_id": login_id, "password": password},
        headers=_request_headers(csrf=_csrf(client)),
    )
    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert response.json()["demo_mode"] is True
    return response.json()["csrf_token"]


def _logout(client: TestClient, csrf: str) -> None:
    response = client.post(
        "/api/v1/auth/logout", json={}, headers=_request_headers(csrf=csrf)
    )
    assert response.status_code == 204


@pytest.mark.integration
def test_customer_a_cannot_read_customer_b_jobs(tmp_path) -> None:
    """Use real demo login/session cookies and owner-authorized HTTP queries."""
    settings = Settings.for_test(tmp_path / "account-isolation", demo_mode=True)
    with TestClient(
        create_app(settings),
        base_url=CANONICAL_ORIGIN,
        client=("127.0.0.1", 43123),
    ) as client:
        anonymous_csrf = _csrf(client)
        credentials_response = client.post(
            "/api/v1/demo/credentials", json={}, headers=_request_headers(csrf=anonymous_csrf)
        )
        assert credentials_response.status_code == 200
        credentials = {item["account_key"]: item for item in credentials_response.json()["accounts"]}

        # One process owns the SQLite root. Exercise independent authenticated
        # sessions serially through one HTTP client; no second server is started
        # and demo restart cannot rotate credentials mid-test.
        csrf = _login(client, credentials["customer.a"]["login_id"], credentials["customer.a"]["password"])
        jobs_a = client.get("/api/v1/me/jobs", headers={"Accept": "application/json"})
        assert jobs_a.status_code == 200
        ids_a = {item["job_id"] for item in jobs_a.json()["items"]}
        assert ids_a
        _logout(client, csrf)

        csrf = _login(client, credentials["customer.b"]["login_id"], credentials["customer.b"]["password"])
        jobs_b = client.get("/api/v1/me/jobs", headers={"Accept": "application/json"})
        assert jobs_b.status_code == 200
        ids_b = {item["job_id"] for item in jobs_b.json()["items"]}
        assert ids_b and ids_a.isdisjoint(ids_b)
        foreign_job_id = next(iter(ids_b))
        _logout(client, csrf)

        stable_request_id = str(uuid.uuid4())
        _login(client, credentials["customer.a"]["login_id"], credentials["customer.a"]["password"])
        foreign = client.get(
            f"/api/v1/me/jobs/{foreign_job_id}",
            headers={"Accept": "application/json", "X-Request-ID": stable_request_id},
        )
        absent = client.get(
            f"/api/v1/me/jobs/{uuid.uuid4()}",
            headers={"Accept": "application/json", "X-Request-ID": stable_request_id},
        )
        assert foreign.status_code == absent.status_code == 404
        assert foreign.content == absent.content
        assert foreign.json()["error"]["code"] == "NOT_FOUND"
