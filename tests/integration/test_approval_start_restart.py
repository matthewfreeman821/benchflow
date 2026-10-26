"""Exact-version customer approval, technician start, and durable restart."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from config.settings import CANONICAL_ORIGIN, Settings
from backend.app import create_app
from backend.testing import seed_approval_start_scenario


def _headers(csrf: str, request_id: str | None = None) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": CANONICAL_ORIGIN,
        "X-CSRF-Token": csrf,
        "X-Request-ID": request_id or str(uuid.uuid4()),
    }


def _login(client: TestClient, login_id: str, password: str) -> str:
    session = client.get("/api/v1/session", headers={"Accept": "application/json"})
    assert session.status_code == 200
    response = client.post(
        "/api/v1/auth/login",
        json={"login_id": login_id, "password": password},
        headers=_headers(session.json()["csrf_token"]),
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


@pytest.mark.integration
def test_approved_current_scope_can_start_and_survives_restart(tmp_path) -> None:
    """The test fixture provides prerequisites, not a mocked command result."""
    settings = Settings.for_test(tmp_path / "approval-start", demo_mode=False)
    scenario = seed_approval_start_scenario(settings)

    with TestClient(create_app(settings), base_url=CANONICAL_ORIGIN) as customer:
        customer_csrf = _login(customer, scenario.customer_login_id, scenario.customer_password)
        approval = customer.post(
            "/api/v1/commands",
            json={
                "command_id": str(uuid.uuid4()),
                "target": {"type": "job", "id": scenario.job_id},
                "expected": {
                    "job_rev": scenario.job_rev,
                    "gate_rev": scenario.gate_rev,
                    "estimate_version": scenario.estimate_version,
                },
                "action": "estimate.decision",
                "data": {
                    "decision": "approve",
                    "version": scenario.estimate_version,
                    "method": "customer_account",
                    "attribution": None,
                    "confirmation": {
                        "version": scenario.estimate_version,
                        "total_minor": scenario.total_minor,
                        "currency": scenario.currency,
                        "expires_at": scenario.expires_at,
                    },
                },
            },
            headers=_headers(customer_csrf),
        )
        assert approval.status_code == 200
        assert approval.json()["status"] == "applied"

    with TestClient(create_app(settings), base_url=CANONICAL_ORIGIN) as technician:
        technician_csrf = _login(
            technician, scenario.technician_login_id, scenario.technician_password
        )
        detail = technician.get(
            f"/api/v1/staff/jobs/{scenario.job_id}",
            headers={"Accept": "application/json"},
        )
        assert detail.status_code == 200
        assert detail.json()["summary"]["repair_gate"] == "open"
        current_job_rev = detail.json()["job_rev"]
        current_gate_rev = detail.json()["gate_rev"]

        start = technician.post(
            "/api/v1/commands",
            json={
                "command_id": str(uuid.uuid4()),
                "target": {"type": "job", "id": scenario.job_id},
                "expected": {
                    "job_rev": current_job_rev,
                    "gate_rev": current_gate_rev,
                    "estimate_version": scenario.estimate_version,
                    **{
                        f"stock_rev:{part_id}": stock_rev
                        for part_id, stock_rev in scenario.stock_revisions.items()
                    },
                },
                "action": "work.start",
                "data": {
                    "task_id": scenario.task_id,
                    "scope_ref": scenario.scope_ref,
                    "consume": [],
                },
            },
            headers=_headers(technician_csrf),
        )
        assert start.status_code == 200
        assert start.json()["status"] == "applied"

    # A new app/client over the same data root proves committed state survives
    # app teardown/recreation; this is not a process-level deployment test.
    with TestClient(create_app(settings), base_url=CANONICAL_ORIGIN) as restarted:
        _login(restarted, scenario.technician_login_id, scenario.technician_password)
        restored = restarted.get(
            f"/api/v1/staff/jobs/{scenario.job_id}",
            headers={"Accept": "application/json"},
        )
        assert restored.status_code == 200
        task = next(item for item in restored.json()["work"] if item["task_id"] == scenario.task_id)
        assert task["state"] == "active"
        assert restored.json()["summary"]["repair_gate"] == "open"
