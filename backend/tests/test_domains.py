"""Substantive transactional service tests on isolated fictional SQLite databases."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import copy

import pytest

from config.settings import Settings
from contracts.actions import ACTION_NAMES, parse_command
from backend import commands, views
from backend.core import Rejected, account_actor, insert, later, one, rows, uid
from backend.database import connect, immediate_transaction, utc_now
from backend.repairs import current_estimate
from backend.testing import UNKNOWN, approve, fixture_account, new_customer, run, scenario, seed_approval_start_scenario


@pytest.fixture
def shop(tmp_path):
    settings = Settings.for_test(tmp_path / "shop")
    s = seed_approval_start_scenario(settings)
    with closing(connect(settings)) as db:
        manager = one(db, "SELECT id FROM accounts WHERE login_id='fixture.manager'")["id"]
        tech = one(db, "SELECT id FROM accounts WHERE login_id='fixture.technician'")["id"]
        customer = one(db, "SELECT customer_id FROM jobs WHERE id=?", (s.job_id,))["customer_id"]
    return settings, s, manager, tech, customer


def act(shop, actor, action, data, target=None, *, now=None, expected=None, demo_mode=True):
    with closing(connect(shop[0])) as db, immediate_transaction(db):
        return run(db, actor, action, data, now or utc_now(), target, expected, demo_mode=demo_mode)


def approve_shop(shop):
    with closing(connect(shop[0])) as db, immediate_transaction(db):
        return approve(db, shop[2], shop[1].job_id, utc_now())


def counts(db):
    return tuple(one(db, f"SELECT count(*) n FROM {table}")["n"] for table in (
        "audit_events", "idempotency_results", "estimate_decisions", "stock_movements", "settlement_entries"))


def test_catalog_matches_shared_actions():
    assert set(commands.HANDLERS) == ACTION_NAMES


@pytest.mark.parametrize("offset,allowed", [(-1, True), (0, False), (1, False)])
def test_acceptance_deadline_is_strict_server_time(shop, offset, allowed):
    with closing(connect(shop[0])) as db:
        e = current_estimate(db, shop[1].job_id)
    now = later(e["expires_at"], seconds=offset)
    if allowed:
        act(shop, shop[2], "estimate.decision", dict(decision="approve", version=1, method="phone", attribution="Fictional caller",
            confirmation=dict(version=1, total_minor=e["total_minor"], currency="USD", expires_at=e["expires_at"])), ("job", shop[1].job_id), now=now)
    else:
        with closing(connect(shop[0])) as db:
            before = counts(db)
        with pytest.raises(Rejected, match="ESTIMATE_WINDOW_CLOSED"):
            act(shop, shop[2], "estimate.decision", dict(decision="approve", version=1, method="phone", attribution="Fictional caller",
                confirmation=dict(version=1, total_minor=e["total_minor"], currency="USD", expires_at=e["expires_at"])), ("job", shop[1].job_id), now=now)
        with closing(connect(shop[0])) as db:
            assert counts(db) == before


def test_timely_unchanged_approval_survives_expiry_and_restart(shop):
    approve_shop(shop)
    act(shop, shop[3], "work.start", dict(task_id=shop[1].task_id, scope_ref=shop[1].scope_ref, consume=[]),
        ("job", shop[1].job_id), now=later(shop[1].expires_at, days=1))
    with closing(connect(shop[0])) as db:
        assert one(db, "SELECT state FROM work_tasks WHERE id=?", (shop[1].task_id,))["state"] == "active"
        assert views.job_detail(db, account_actor(db, shop[3]), shop[1].job_id, utc_now())["summary"]["repair_gate"] == "open"


def test_stale_idempotency_and_actor_key_conflict_are_atomic(shop):
    with closing(connect(shop[0])) as db:
        j = one(db, "SELECT * FROM jobs WHERE id=?", (shop[1].job_id,))
    payload = dict(command_id=uid(), action="estimate.decision", target=dict(type="job", id=j["id"]),
        expected=dict(job_rev=j["job_rev"], gate_rev=j["gate_rev"], estimate_version=1),
        data=dict(decision="approve", version=1, method="phone", attribution="Fictional caller", confirmation=dict(
            version=1, total_minor=shop[1].total_minor, currency="USD", expires_at=shop[1].expires_at)))
    with closing(connect(shop[0])) as db, immediate_transaction(db):
        first = commands.execute(db, parse_command(payload), account_actor(db, shop[2]), utc_now())
        after = counts(db)
    with closing(connect(shop[0])) as db, immediate_transaction(db):
        second = commands.execute(db, parse_command(payload), account_actor(db, shop[2]), utc_now())
        assert second["status"] == "replayed" and second["event_id"] == first["event_id"]
        assert counts(db) == after
    changed = copy.deepcopy(payload)
    changed["data"]["decision"] = "decline"
    with closing(connect(shop[0])) as db, pytest.raises(Rejected, match="IDEMPOTENCY_KEY_REUSED"), immediate_transaction(db):
        commands.execute(db, parse_command(changed), account_actor(db, shop[2]), utc_now())
    stale = copy.deepcopy(payload)
    stale["command_id"] = uid()
    with closing(connect(shop[0])) as db, pytest.raises(Rejected, match="STALE_REVISION"), immediate_transaction(db):
        commands.execute(db, parse_command(stale), account_actor(db, shop[2]), utc_now())
    with closing(connect(shop[0])) as db:
        assert counts(db) == after


def test_foreign_child_is_404_before_stale_and_no_changes(shop):
    with closing(connect(shop[0])) as db, immediate_transaction(db):
        other = new_customer(db, shop[2], "Fictional other", utc_now())
        _, other_task, _ = scenario(db, shop[2], shop[3], other, utc_now())
        before = counts(db)
    payload = dict(command_id=uid(), action="work.start", target=dict(type="job", id=shop[1].job_id),
        expected=dict(job_rev=1, gate_rev=1, estimate_version=1),
        data=dict(task_id=other_task, scope_ref=shop[1].scope_ref, consume=[]))
    with closing(connect(shop[0])) as db, pytest.raises(Rejected, match="NOT_FOUND"), immediate_transaction(db):
        commands.execute(db, parse_command(payload), account_actor(db, shop[3]), utc_now())
    with closing(connect(shop[0])) as db:
        assert counts(db) == before


def test_diagnostic_permission_never_substitutes_for_repair_approval(shop):
    with pytest.raises(Rejected, match="SCOPE_NOT_APPROVED"):
        act(shop, shop[3], "work.start", dict(task_id=shop[1].task_id, scope_ref=shop[1].scope_ref, consume=[]),
            ("job", shop[1].job_id))
    with closing(connect(shop[0])) as db, immediate_transaction(db):
        j = one(db, "SELECT * FROM jobs WHERE id=?", (shop[1].job_id,))
        perm = one(db, "SELECT * FROM diagnostic_permissions WHERE job_id=?", (j["id"],))
        run(db, shop[2], "diagnostic.permission", dict(decision="revoke", scope=perm["scope"], fee=dict(currency="USD", amount_minor=0),
            method="phone", attribution="Fictional caller", expected_decision_id=perm["id"]), utc_now(), ("job", j["id"]))
    with pytest.raises(Rejected, match="SCOPE_NOT_APPROVED"):
        act(shop, shop[3], "diagnostic.record", dict(task_id=None, kind="test", summary="Not allowed",
            customer_text=None, customer_visible=False, uncertainty=None), ("job", shop[1].job_id))


def test_material_revision_safe_stop_and_future_scope_hold(shop):
    approve_shop(shop)
    target = ("job", shop[1].job_id)
    act(shop, shop[3], "work.start", dict(task_id=shop[1].task_id, scope_ref=shop[1].scope_ref, consume=[]), target)
    draft = act(shop, shop[3], "estimate.draft_save", dict(draft_id=None, lines=[dict(kind="labor", description="Revised repair",
        quantity=1, unit_amount_minor=15000, scope_ref=uid())], currency="USD", tax_minor=0, tax_label="No tax"), target)["result"]["id"]
    act(shop, shop[2], "estimate.issue", dict(draft_id=draft, expires_at=later(utc_now(), days=7), change_reason="Revised price"), target)
    with pytest.raises(Rejected, match="REVISION_HOLD"):
        act(shop, shop[3], "work.log", dict(task_id=shop[1].task_id, kind="complete", summary="Cannot complete through hold",
            owner_staff_id=None, next_action=None, next_action_due_at=None), target)
    act(shop, shop[3], "work.log", dict(task_id=shop[1].task_id, kind="pause", summary="Safe stop reached",
        owner_staff_id=shop[3], next_action="Request revised approval", next_action_due_at=later(utc_now(), days=1)), target)
    with closing(connect(shop[0])) as db:
        assert one(db, "SELECT safe_stop_required FROM jobs WHERE id=?", (shop[1].job_id,))["safe_stop_required"] == 0
        assert one(db, "SELECT count(*) n FROM estimates WHERE job_id=?", (shop[1].job_id,))["n"] == 2
        assert one(db, "SELECT count(*) n FROM estimate_decisions WHERE decision='approved'")["n"] == 1


def test_two_jobs_last_unit_race_has_one_winner(shop):
    approve_shop(shop)
    target1 = ("job", shop[1].job_id)
    part = act(shop, shop[2], "parts.create", dict(sku="RACE", description="Fictional cable", unit="each",
        initial_qty=1, reason="manual_record"))["aggregate"]["id"]
    with closing(connect(shop[0])) as db, immediate_transaction(db):
        job2, _, scope2 = scenario(db, shop[2], shop[3], shop[4], utc_now(), label="Second display")
        approve(db, shop[2], job2, utc_now())
    scopes = [(target1, shop[1].scope_ref), (("job", job2), scope2)]
    for target, scope in scopes:
        act(shop, shop[3], "parts.demand", dict(part_id=part, job_id=target[1], qty=1, scope_ref=scope, needed_by=None), target)
    def reserve(pair):
        target, scope = pair
        try:
            act(shop, shop[3], "parts.reserve", dict(part_id=part, job_id=target[1], qty=1, scope_ref=scope, reason=None), target)
            return "reserved"
        except Rejected as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, scopes))
    assert outcomes.count("reserved") == 1 and outcomes.count("STOCK_SHORTAGE") == 1
    with closing(connect(shop[0])) as db:
        assert one(db, "SELECT reserved_qty FROM parts WHERE id=?", (part,))["reserved_qty"] == 1


def test_exact_multipart_vector_rejects_missing_extra_stale_atomically(shop):
    approve_shop(shop)
    target = ("job", shop[1].job_id)
    parts = []
    for index in range(2):
        part = act(shop, shop[2], "parts.create", dict(sku=f"VECTOR-{index}", description="Fictional part", unit="each",
            initial_qty=1, reason="manual_record"))["aggregate"]["id"]
        act(shop, shop[3], "parts.demand", dict(part_id=part, job_id=target[1], qty=1, scope_ref=shop[1].scope_ref, needed_by=None), target)
        r = act(shop, shop[3], "parts.reserve", dict(part_id=part, job_id=target[1], qty=1, scope_ref=shop[1].scope_ref, reason=None), target)["result"]["id"]
        parts.append((part, r))
    with closing(connect(shop[0])) as db:
        vector = {f"stock_rev:{part}": one(db, "SELECT stock_rev FROM parts WHERE id=?", (part,))["stock_rev"] for part, _ in parts}
        before = counts(db)
    data = dict(task_id=shop[1].task_id, scope_ref=shop[1].scope_ref, consume=[dict(reservation_id=r, qty=1) for _, r in parts])
    for bad, code in [
        ({k: v for k, v in list(vector.items())[:1]}, "VALIDATION_FAILED"),
        ({**vector, "stock_rev:" + uid(): 1}, "VALIDATION_FAILED"),
        ({k: v - 1 for k, v in vector.items()}, "STALE_REVISION"),
    ]:
        with pytest.raises(Rejected, match=code):
            act(shop, shop[3], "work.start", data, target, expected=bad)
        with closing(connect(shop[0])) as db:
            assert counts(db) == before
    act(shop, shop[3], "work.start", data, target, expected=vector)
    with closing(connect(shop[0])) as db:
        assert all(r["on_hand_qty"] == 0 and r["reserved_qty"] == 0 for r in rows(db, "SELECT * FROM parts"))


def test_qc_failure_requires_linked_completed_rework(shop):
    approve_shop(shop)
    target = ("job", shop[1].job_id)
    act(shop, shop[3], "work.start", dict(task_id=shop[1].task_id, scope_ref=shop[1].scope_ref, consume=[]), target)
    act(shop, shop[3], "work.log", dict(task_id=shop[1].task_id, kind="complete", summary="Done", owner_staff_id=None,
        next_action=None, next_action_due_at=None), target)
    with closing(connect(shop[0])) as db:
        checks = rows(db, "SELECT c.id FROM qc_profile_checks c JOIN work_tasks t ON t.profile_version_id=c.profile_version_id WHERE t.id=?", (shop[1].task_id,))
    qc_data = dict(task_id=shop[1].task_id, profile_version=1, checks=[dict(check_id=c["id"], result="fail", note=None) for c in checks],
        overall="fail", reason="manual_record")
    act(shop, shop[3], "qc.record", qc_data, target)
    qc_data["overall"] = "pass"
    for c in qc_data["checks"]:
        c["result"] = "pass"
    with pytest.raises(Rejected, match="QC_INCOMPLETE"):
        act(shop, shop[3], "qc.record", qc_data, target)
    with closing(connect(shop[0])) as db:
        rework = one(db, "SELECT id FROM work_tasks WHERE rework_of_task_id=?", (shop[1].task_id,))["id"]
    act(shop, shop[3], "work.start", dict(task_id=rework, scope_ref=shop[1].scope_ref, consume=[]), target)
    act(shop, shop[3], "work.log", dict(task_id=rework, kind="complete", summary="Corrected", owner_staff_id=None,
        next_action=None, next_action_due_at=None), target)
    act(shop, shop[3], "qc.record", qc_data, target)
    with closing(connect(shop[0])) as db:
        assert views.job_detail(db, account_actor(db, shop[3]), target[1], utc_now())["summary"]["qc_state"] == "passed"
        assert one(db, "SELECT count(*) n FROM qc_runs WHERE overall IN ('pass','fail')")["n"] == 2
        assert one(db, "SELECT count(*) n FROM qc_runs WHERE overall='not_tested'")["n"] == 2


def test_refund_caps_and_corrections_rollback_money_and_events(shop):
    target = ("job", shop[1].job_id)
    payment = act(shop, shop[2], "settlement.payment_record", dict(job_id=target[1], amount_minor=10000,
        currency="USD", method="cash", reference=None, received_at=None), target, demo_mode=False)["result"]["id"]
    act(shop, shop[2], "settlement.refund_record", dict(job_id=target[1], payment_id=payment, amount_minor=8000,
        currency="USD", reference="External receipt", refunded_at=None, reason="customer_request"), target, demo_mode=False)
    with closing(connect(shop[0])) as db:
        before = counts(db)
    with pytest.raises(Rejected, match="REFUND_LIMIT"):
        act(shop, shop[2], "settlement.refund_record", dict(job_id=target[1], payment_id=payment, amount_minor=3000,
            currency="USD", reference=None, refunded_at=None, reason="customer_request"), target, demo_mode=False)
    with pytest.raises(Rejected, match="REFUND_LIMIT"):
        act(shop, shop[2], "settlement.correct", dict(entry_id=payment, delta_minor=-5000, currency="USD",
            external_reference="Fictional correction receipt", reason="record_correction"), target, demo_mode=False)
    with closing(connect(shop[0])) as db:
        assert counts(db) == before
        assert views.settlement_view(db, target[1])["balance_minor"] == -2000


def test_cancel_unrepaired_handover_never_requires_qc_or_implies_collection(shop):
    target = ("job", shop[1].job_id)
    act(shop, shop[2], "job.record_contact_attempt", dict(channel="phone", outcome="no_answer", summary="Manual call", occurred_at=None), target)
    act(shop, shop[2], "job.cancel", dict(requested_by="customer", requester_attribution="Fictional verified caller", reason="customer_request"), target)
    with closing(connect(shop[0])) as db:
        assert one(db, "SELECT lifecycle FROM jobs WHERE id=?", (target[1],))["lifecycle"] == "cancelled"
        assert not one(db, "SELECT 1 FROM handovers", required=False)
    data = dict(job_id=target[1], handover_kind="return_unrepaired", collector_name="Fictional customer",
        acknowledgement="Returned without repair", manager_balance_override_reason=None)
    with pytest.raises(Rejected, match="NOT_FOUND"):
        act(shop, shop[2], "property.handover", data, target, demo_mode=False)
    act(shop, shop[2], "property.authorize_collector", dict(job_id=target[1], collector_name="Fictional customer", method="in_person", reason=None), target, demo_mode=False)
    act(shop, shop[2], "property.handover", data, target, demo_mode=False)
    returned = act(shop, shop[2], "return.create", dict(original_job_id=target[1], reported_recurrence="New recurrence",
        customer_attribution="Fictional customer"), target)
    assert returned["result"]["linked_job_id"] != target[1]
    with closing(connect(shop[0])) as db:
        linked = one(db, "SELECT * FROM jobs WHERE id=?", (returned["result"]["linked_job_id"],))
        assert linked["owner_queue"] == "manager_intake" and linked["lifecycle"] == "open"
        assert not one(db, "SELECT 1 FROM diagnostic_permissions WHERE job_id=?", (linked["id"],), required=False)
        assert one(db, "SELECT lifecycle FROM jobs WHERE id=?", (target[1],))["lifecycle"] == "closed"
    linked_target = ("job", linked["id"])
    with pytest.raises(Rejected, match="NOT_FOUND"):
        act(shop, shop[2], "return.assess", dict(return_case_id=returned["result"]["id"], coverage="covered",
            reason="coverage_review", inspection_permission_id=uid()), linked_target)
    permitted = act(shop, shop[2], "diagnostic.permission", dict(decision="grant", scope="Independent recurrence inspection",
        fee=dict(currency="USD", amount_minor=0), method="phone", attribution="Fictional verified caller",
        expected_decision_id=None), linked_target)
    act(shop, shop[2], "return.assess", dict(return_case_id=returned["result"]["id"], coverage="not_covered",
        reason="coverage_review", inspection_permission_id=permitted["result"]["id"]), linked_target)
    with pytest.raises(Rejected, match="STALE_REVISION"):
        act(shop, shop[2], "return.assess", dict(return_case_id=returned["result"]["id"], coverage="covered",
            reason="coverage_review", inspection_permission_id=permitted["result"]["id"]), linked_target,
            expected={k:permitted["job"][k] for k in ("job_rev","gate_rev")})
    with closing(connect(shop[0])) as db:
        assert views.return_view(db, one(db, "SELECT * FROM return_cases WHERE id=?", (returned["result"]["id"],)))["coverage"] == "not_covered"
        assert not current_estimate(db, linked["id"])
        case = views.return_view(db, one(db, "SELECT * FROM return_cases WHERE id=?", (returned["result"]["id"],)))
        assert case["return_rev"] == 2
        assert case["original_job_rev"] > 0 and case["return_job_rev"] > permitted["job"]["job_rev"]


@pytest.mark.parametrize("source_account,target_account", [(False, False), (True, False), (False, True), (True, True)])
def test_verified_merge_all_four_account_branches(shop, source_account, target_account):
    now = utc_now()
    with closing(connect(shop[0])) as db, immediate_transaction(db):
        source = new_customer(db, shop[2], "Fictional source", now)
        target = new_customer(db, shop[2], "Fictional target", now)
        accounts = {}
        for c, enabled in ((source, source_account), (target, target_account)):
            if enabled:
                account = fixture_account(db, "customer", "Fictional duplicate", "dupe." + uid(), "fictional passphrase", now)
                accounts[c] = account
                insert(db, "customer_accounts", customer_id=c, account_id=account, verified_at=now,
                       verified_by_staff_id=shop[2], verification_method="phone")
        run(db, shop[2], "customer.merge", dict(source_customer_id=source, target_customer_id=target,
            verification_method="phone", reason="verified_duplicate_merge"), now,
            expected=dict(source_customer_rev=1, target_customer_rev=1))
        links = rows(db, "SELECT * FROM customer_accounts WHERE customer_id IN (?,?)", (source, target))
        assert len(links) == int(source_account or target_account)
        if links:
            assert links[0]["customer_id"] == target
            assert links[0]["account_id"] == (accounts[target] if target_account else accounts[source])
        if source_account and target_account:
            assert account_actor(db, accounts[source])["state"] == "closed"


def test_owner_contact_request_staff_confirmation_and_unlink(shop):
    with closing(connect(shop[0])) as db:
        customer_actor = one(db, "SELECT account_id FROM customer_accounts WHERE customer_id=?", (shop[4],))["account_id"]
    request = act(shop, customer_actor, "contact.change_request", dict(requested_fields=["phone"],
        display_name=UNKNOWN, phone=dict(value="555-fictional", known=True), email=UNKNOWN))["result"]["id"]
    with closing(connect(shop[0])) as db:
        detail = views.contact_detail(db, request)
        assert detail["requested_values"]["email"] is None and detail["requested_values"]["display_name"] is None
    act(shop, shop[2], "contact.change_resolve", dict(request_id=request, decision="accept", confirmation_method="phone",
        reason_code="contact_resolution"), expected=dict(request_rev=detail["request_rev"], customer_rev=detail["customer_rev"]))
    with closing(connect(shop[0])) as db:
        assert one(db, "SELECT phone_value FROM customers WHERE id=?", (shop[4],))["phone_value"] == "555-fictional"
        a = account_actor(db, customer_actor)
        c = one(db, "SELECT customer_rev FROM customers WHERE id=?", (shop[4],))
    act(shop, shop[2], "account.unlink_customer", dict(account_id=customer_actor, customer_id=shop[4], reason="mistaken_link"),
        expected=dict(account_rev=a["account_rev"], customer_rev=c["customer_rev"]))
    with closing(connect(shop[0])) as db:
        assert account_actor(db, customer_actor)["state"] == "closed"
        assert not one(db, "SELECT 1 FROM customer_accounts WHERE account_id=?", (customer_actor,), required=False)


def test_admin_account_invite_reissue_revoke_suspend_reactivate_close_and_last_manager(shop):
    def account_action(action, account_id, **values):
        with closing(connect(shop[0])) as db:
            a = account_actor(db, account_id)
        return act(shop, shop[2], action, dict(account_id=account_id, **values), expected=dict(account_rev=a["account_rev"]))
    invited = act(shop, shop[2], "account.invite_staff", dict(display_name="Fictional new staff", roles=["intake"],
        reason="manual_record", owner_staff_id=shop[2], next_action_due_at=later(utc_now(), hours=48)))["aggregate"]["id"]
    first = account_action("account.reissue_invite", invited, reason="customer_request")
    assert first["result"]["one_time_code"]
    account_action("account.revoke_invite", invited, reason_code="invite_revocation")
    with closing(connect(shop[0])) as db:
        assert one(db, "SELECT count(*) n FROM auth_codes WHERE account_id=? AND state='pending'", (invited,))["n"] == 0
    account_action("account.close", invited, reason="customer_request")
    account_action("account.suspend", shop[3], reason="suspension")
    with closing(connect(shop[0])) as db:
        assert one(db, "SELECT owner_queue FROM jobs WHERE id=?", (shop[1].job_id,))["owner_queue"] == "manager_intake"
    account_action("account.reactivate", shop[3], reason="reactivation")
    with pytest.raises(Rejected, match="FORBIDDEN"):
        account_action("account.change_roles", shop[2], roles=["intake"], reason="role_change")
    from backend.customers import last_manager
    with closing(connect(shop[0])) as db, pytest.raises(Rejected, match="LAST_MANAGER"):
        last_manager(db, account_actor(db, shop[2]))


def test_customer_contact_update_followup_assignment_cancel_reopen_revoke(shop):
    act(shop, shop[2], "customer.update_contact", dict(display_name="Fictional renamed customer", phone=UNKNOWN, email=UNKNOWN,
        reason="record_correction"), ("customer", shop[4]), expected=dict(customer_rev=1))
    target = ("job", shop[1].job_id)
    act(shop, shop[2], "job.assign", dict(staff_ids=[shop[3], shop[2]], owner_staff_id=shop[3], reason="manual_record"), target)
    act(shop, shop[3], "job.update_follow_up", dict(owner_staff_id=shop[3], next_action="Review bench findings",
        next_action_due_at=later(utc_now(), hours=1), state="due", reason="manual_record"), target)
    act(shop, shop[2], "job.update_follow_up", dict(owner_staff_id=None, next_action=None, next_action_due_at=None,
        state="resolved", reason="manual_record"), target)
    approve_shop(shop)
    act(shop, shop[2], "estimate.revoke", dict(version=1, reason="customer_request", attribution="Fictional caller", method="phone"), target)
    act(shop, shop[2], "job.cancel", dict(requested_by="staff", requester_attribution="Fictional correction", reason="record_correction"), target)
    act(shop, shop[2], "job.reopen", dict(reason="record_correction", next_action="Issue fresh approval request",
        next_action_due_at=later(utc_now(), hours=24)), target)
    with pytest.raises(Rejected, match="REVISION_HOLD"):
        act(shop, shop[3], "work.start", dict(task_id=shop[1].task_id, scope_ref=shop[1].scope_ref, consume=[]), target)


def test_order_receive_reserve_release_consume_and_inspected_stock_return(shop):
    approve_shop(shop)
    target = ("job", shop[1].job_id)
    part = act(shop, shop[2], "parts.create", dict(sku="FULL-STOCK", description="Fictional parts", unit="each",
        initial_qty=0, reason="manual_record"))["aggregate"]["id"]
    act(shop, shop[3], "parts.demand", dict(part_id=part, job_id=target[1], qty=2, scope_ref=shop[1].scope_ref, needed_by=None), target)
    order = act(shop, shop[2], "parts.manual_order", dict(part_id=part, job_id=target[1], qty=2,
        supplier_label="Fictional supplier", eta_at=None, eta_evidence=None, placement_state="placed_unconfirmed",
        reason="manual_record"), target)["result"]["id"]
    act(shop, shop[2], "parts.record_order_outcome", dict(order_id=order, outcome="supplier_confirmed",
        evidence="Manually recorded fictional phone call", reason="manual_record"), target)
    act(shop, shop[2], "parts.receive", dict(order_id=order, part_id=part, qty=2, source_note="Fictional receipt"), target)
    reservation = act(shop, shop[3], "parts.reserve", dict(part_id=part, job_id=target[1], qty=2,
        scope_ref=shop[1].scope_ref, reason=None), target)["result"]["id"]
    with closing(connect(shop[0])) as db:
        rev = one(db, "SELECT stock_rev FROM parts WHERE id=?", (part,))["stock_rev"]
    act(shop, shop[3], "work.start", dict(task_id=shop[1].task_id, scope_ref=shop[1].scope_ref, consume=[]),
        target, expected={"stock_rev:" + part: rev})
    act(shop, shop[3], "work.consume", dict(task_id=shop[1].task_id, items=[dict(reservation_id=reservation, qty=1)]),
        target, expected={"stock_rev:" + part: rev})
    act(shop, shop[3], "parts.release", dict(reservation_id=reservation, qty=1, reason="customer_request"), target)
    with closing(connect(shop[0])) as db:
        source = one(db, "SELECT id FROM stock_movements WHERE kind='consumption' AND part_id=?", (part,))["id"]
    act(shop, shop[2], "parts.adjust", dict(part_id=part, delta_qty=1, source="return_to_stock",
        source_movement_id=source, inspection_note="Fictional inspected unused replacement", reason="inspected_return"))
    with pytest.raises(Rejected, match="VALIDATION_FAILED"):
        act(shop, shop[2], "parts.adjust", dict(part_id=part, delta_qty=1, source="return_to_stock",
            source_movement_id=source, inspection_note="Cannot return twice", reason="inspected_return"))
    with closing(connect(shop[0])) as db:
        part_view = views.part_detail(db, part)
        assert part_view["on_hand_qty"] == 2 and part_view["reserved_qty"] == 0


def test_profile_version_pinning_money_credit_and_policy(shop):
    with closing(connect(shop[0])) as db:
        profile = one(db, "SELECT * FROM qc_profiles")
    act(shop, shop[2], "qc.profile_update", dict(profile_id=profile["id"], checks=[dict(label="New required check", required=True)],
        reason="record_correction"), expected=dict(profile_rev=profile["profile_rev"]))
    with closing(connect(shop[0])) as db:
        old_task = one(db, "SELECT v.version FROM work_tasks t JOIN qc_profile_versions v ON v.id=t.profile_version_id WHERE t.id=?", (shop[1].task_id,))
        assert old_task["version"] == 1
        assert views.profile(db, profile["id"])["version"] == 2
    payment = act(shop, shop[2], "settlement.payment_record", dict(job_id=shop[1].job_id, amount_minor=5000, currency="USD",
        method="cash", reference=None, received_at=None), ("job", shop[1].job_id), demo_mode=False)["result"]["id"]
    act(shop, shop[2], "settlement.correct", dict(entry_id=payment, delta_minor=-1000, currency="USD",
        external_reference="Fictional record correction receipt", reason="record_correction"), ("job", shop[1].job_id))
    act(shop, shop[2], "settlement.credit", dict(job_id=shop[1].job_id, charge_id=None, amount_minor=100,
        currency="USD", kind="customer_credit", reason="customer_request"), ("job", shop[1].job_id))
    with closing(connect(shop[0])) as db, immediate_transaction(db):
        policy_id = insert(db, "shop_policy", id=uid(), shop_timezone="America/New_York", estimate_window_days=7,
            diagnostic_fee_minor=0, currency="USD", tax_enabled=0, tax_label="No tax")
        run(db, shop[2], "policy.update_demo", dict(shop_timezone="America/New_York", estimate_window_days=5,
            diagnostic_fee_minor=0, tax_enabled=False, reason="manual_record"), utc_now(), expected=dict(policy_rev=1))
