"""Named read API with caller/route/filter-scoped opaque pagination."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

from contracts import projections as p
from backend import views
from backend.core import Rejected, eligible_staff, encode, one, require, rows, visible_job
from backend.customers import normalized


def staff(actor, roles=None):
    require(actor["kind"] == "staff", "FORBIDDEN")
    require(not roles or bool(set(actor["roles"]) & set(roles)), "FORBIDDEN")


def duplicates(db, c):
    result = []
    for other in rows(db, "SELECT * FROM customers WHERE id!=? AND merged_into_id IS NULL AND "
                      "(name_normalized=? OR (phone_normalized IS NOT NULL AND phone_normalized=?) OR "
                      "(email_normalized IS NOT NULL AND email_normalized=?))",
                      (c["id"], c["name_normalized"], c["phone_normalized"], c["email_normalized"])):
        reasons = [key for key in ("name", "phone", "email") if c[key + "_normalized"] and
                   c[key + "_normalized"] == other[key + "_normalized"]]
        result.append(dict(customer_id=other["id"], display_name=other["display_name"], match_reasons=reasons))
    return result


def customer_detail(db, c, now):
    link = one(db, "SELECT account_id FROM customer_accounts WHERE customer_id=?", (c["id"],), required=False)
    return p.CustomerStaffDetail(customer_id=c["id"], display_name=c["display_name"], phone=views.known(c, "phone"),
        email=views.known(c, "email"), notes=c["notes"], possible_duplicates=duplicates(db, c),
        account_id=link["account_id"] if link else None, customer_rev=c["customer_rev"],
        jobs=[views.queue_item(db, j, now) for j in rows(db, "SELECT * FROM jobs WHERE customer_id=? ORDER BY opened_at,id", (c["id"],))]).model_dump(mode="json")


def page(items, actor, route, query, secret):
    limit_text = query.get("limit", "50")
    require(limit_text.isascii() and limit_text.isdecimal() and 1 <= int(limit_text) <= 100, "INVALID_REQUEST")
    limit = int(limit_text)
    scope = encode([actor["id"], route, {k: v for k, v in query.items() if k != "cursor"}])
    start = 0
    cursor = query.get("cursor")
    if cursor:
        try:
            payload = base64.urlsafe_b64decode(cursor.encode()).decode()
            offset, signature = payload.split(".")
            expected = hmac.new(secret, (scope + ":" + offset).encode(), hashlib.sha256).hexdigest()
            require(hmac.compare_digest(signature, expected) and offset.isdecimal(), "INVALID_REQUEST")
            start = int(offset)
        except (ValueError, UnicodeError):
            raise Rejected("INVALID_REQUEST") from None
    next_cursor = None
    if start + limit < len(items):
        offset = str(start + limit)
        signature = hmac.new(secret, (scope + ":" + offset).encode(), hashlib.sha256).hexdigest()
        next_cursor = base64.urlsafe_b64encode((offset + "." + signature).encode()).decode()
    return dict(items=items[start:start + limit], next_cursor=next_cursor)


def read(db, actor, route, query, now, secret):
    """route is the canonical suffix under /api/v1, not a SQL identifier."""
    common = {"cursor", "limit"}
    allowed = {
        "staff/queue": common | {"bucket"}, "staff/jobs": common | {"query"},
        "staff/customers": common | {"query"}, "staff/parts": common | {"query", "job_id"},
        "staff/accounts": common | {"state"}, "staff/contact-change-requests": common | {"state"},
        "staff/qc-profiles": common, "staff/audit": common | {"aggregate_id", "event_id"},
        "me/jobs": common, "customer/contact-change-requests": common, "staff/assignable-staff": {"job_id"},
    }.get(route, {"job_id"} if route.startswith("staff/parts/") else set())
    require(set(query) <= allowed, "INVALID_REQUEST")
    if "query" in query:
        require(1 <= len(query["query"]) <= 120, "INVALID_REQUEST")
    if route == "me":
        return views.account(db, actor["id"], now)
    if route.startswith("me/jobs"):
        require(actor["kind"] == "customer", "FORBIDDEN")
        if route != "me/jobs":
            return views.job_detail(db, actor, route.removeprefix("me/jobs/"), now)
        items = []
        for j in rows(db, "SELECT * FROM jobs WHERE customer_id=? ORDER BY opened_at,id", (actor["customer_id"],)):
            detail = views.job_detail(db, actor, j["id"], now)
            items.append(p.CustomerJobSummary(job_id=j["id"], job_code=j["job_code"], device_label=views.device_label(db, j),
                opened_at=j["opened_at"], customer_status=detail["status"], updated_at=j["updated_at"]).model_dump(mode="json"))
        return page(items, actor, route, query, secret)
    if route == "customer/contact-change-requests":
        require(actor["kind"] == "customer", "FORBIDDEN")
        result = [p.CustomerContactChangeStatus(**views.contact_status(r)).model_dump(mode="json")
                  for r in rows(db, "SELECT * FROM contact_change_requests WHERE customer_id=? AND account_id=? ORDER BY requested_at,id",
                                (actor["customer_id"], actor["id"]))]
        return page(result, actor, route, query, secret)
    staff(actor)
    if route.startswith("staff/jobs/"):
        return views.job_detail(db, actor, route.removeprefix("staff/jobs/"), now)
    if route in {"staff/queue", "staff/jobs"}:
        bucket = query.get("bucket", "active")
        require(bucket in {"active", "due", "overdue", "reassignment", "manager_intake"}, "INVALID_REQUEST")
        if bucket in {"reassignment", "manager_intake"}:
            staff(actor, {"manager"})
        items = []
        for j in rows(db, "SELECT * FROM jobs ORDER BY next_action_due_at,updated_at,id"):
            try:
                visible_job(db, actor, j["id"])
            except Rejected:
                continue
            if route == "staff/queue":
                if j["lifecycle"] == "closed":
                    continue
                if bucket in {"reassignment", "manager_intake"} and j["owner_queue"] is None:
                    continue
                if bucket in {"due", "overdue"} and j["next_action_due_at"] > now:
                    continue
            item = views.queue_item(db, j, now)
            if query.get("query"):
                c = one(db, "SELECT * FROM customers WHERE id=?", (j["customer_id"],))
                device = one(db, "SELECT * FROM devices WHERE id=?", (j["device_id"],))
                text = " ".join(str(v or "") for v in (j["job_code"], c["name_normalized"], c["phone_normalized"],
                                                       c["email_normalized"], device["search_normalized"]))
                if normalized(query["query"]) not in normalized(text):
                    continue
            items.append(item)
        return page(items, actor, route, query, secret)
    if route.startswith("staff/customers"):
        staff(actor, {"intake", "manager"})
        segments = route.split("/")
        if len(segments) == 4 and segments[-1] == "account":
            # Intentionally customer-scoped. Never expose a staff directory or
            # arbitrary account lookup to intake just to obtain a recovery rev.
            link = one(db, "SELECT ca.account_id FROM customer_accounts ca JOIN customers c ON c.id=ca.customer_id "
                       "JOIN accounts a ON a.id=ca.account_id WHERE ca.customer_id=? AND c.merged_into_id IS NULL "
                       "AND a.kind='customer'", (segments[2],))
            return views.account(db, link["account_id"], now)
        if route != "staff/customers":
            return customer_detail(db, one(db, "SELECT * FROM customers WHERE id=?", (route.removeprefix("staff/customers/"),)), now)
        items = []
        for c in rows(db, "SELECT * FROM customers WHERE merged_into_id IS NULL ORDER BY name_normalized,id"):
            text = " ".join(c[k] or "" for k in ("name_normalized", "phone_normalized", "email_normalized"))
            if query.get("query") and normalized(query["query"]) not in text:
                continue
            link = one(db, "SELECT a.state FROM accounts a JOIN customer_accounts ca ON ca.account_id=a.id WHERE ca.customer_id=?",
                       (c["id"],), required=False)
            items.append(p.CustomerSummary(customer_id=c["id"], display_name=c["display_name"],
                phone_masked=("•••" + c["phone_value"][-2:]) if c["phone_value"] else None,
                email_masked=("•••@" + c["email_value"].split("@")[-1]) if c["email_value"] else None,
                possible_duplicate=bool(duplicates(db, c)), account_state=link["state"] if link else "none",
                updated_at=c["updated_at"]).model_dump(mode="json"))
        return page(items, actor, route, query, secret)
    if route == "staff/assignable-staff":
        require(query.get("job_id"), "INVALID_REQUEST")
        visible_job(db, actor, query["job_id"])
        items = []
        for a in rows(db, "SELECT id FROM accounts WHERE kind='staff' AND state='active' ORDER BY display_name,id"):
            if "manager" not in actor["roles"] and a["id"] != actor["id"] and not one(db,
                "SELECT 1 FROM job_assignments WHERE job_id=? AND staff_id=?", (query["job_id"], a["id"]), required=False):
                continue
            option = eligible_staff(db, a["id"])
            items.append(dict(staff_id=option["id"], display_name=option["display_name"], roles=option["roles"]))
        return p.AssignableStaffDirectory(items=items).model_dump(mode="json")
    if route.startswith("staff/parts"):
        if query.get("job_id"):
            visible_job(db, actor, query["job_id"])
        else:
            staff(actor, {"intake", "manager"})
        if route != "staff/parts":
            segments = route.split("/")
            detail = views.part_detail(db, segments[2])
            if len(segments) == 5 and segments[3] == "movements":
                found = next((m for m in detail["movements"] if m["movement_id"] == segments[4]), None)
                require(found is not None, "NOT_FOUND")
                return found
            require(len(segments) == 3, "NOT_FOUND")
            return detail
        items = []
        for r in rows(db, "SELECT * FROM parts ORDER BY sku,id"):
            if query.get("query") and normalized(query["query"]) not in normalized(r["sku"] + " " + r["description"]):
                continue
            detail = views.part_detail(db, r["id"])
            detail.pop("movements")
            items.append(p.PartSummary(**detail).model_dump(mode="json"))
        return page(items, actor, route, query, secret)
    if route.startswith("staff/contact-change-requests"):
        staff(actor, {"intake", "manager"})
        if route != "staff/contact-change-requests":
            return views.contact_detail(db, route.split("/")[-1])
        state = query.get("state", "pending")
        require(state in {"pending", "resolved"}, "INVALID_REQUEST")
        items = []
        for r in rows(db, "SELECT * FROM contact_change_requests ORDER BY requested_at,id"):
            if (r["state"] == "pending") != (state == "pending"):
                continue
            c = one(db, "SELECT display_name FROM customers WHERE id=?", (r["customer_id"],))
            items.append(p.ContactChangeQueueItem(request_id=r["id"], customer_label=c["display_name"],
                requested_fields=json.loads(r["requested_fields_json"]), state=r["state"], created_at=r["requested_at"]).model_dump(mode="json"))
        return page(items, actor, route, query, secret)
    if route.startswith("staff/return-cases/"):
        staff(actor, {"manager", "intake"})
        case = one(db, "SELECT * FROM return_cases WHERE id=?", (route.split("/")[-1],))
        return views.return_view(db, case, actor)
    if route.startswith("staff/audit"):
        require(not (query.get("aggregate_id") and query.get("event_id")), "INVALID_REQUEST")
        if "manager" not in actor["roles"]:
            id_ = query.get("aggregate_id")
            if query.get("event_id"):
                id_ = one(db, "SELECT aggregate_id FROM audit_events WHERE id=?", (query["event_id"],))["aggregate_id"]
            require(id_, "FORBIDDEN")
            visible_job(db, actor, id_)
        items = []
        for e in rows(db, "SELECT * FROM audit_events ORDER BY occurred_at,id"):
            if query.get("aggregate_id") and e["aggregate_id"] != query["aggregate_id"]:
                continue
            if query.get("event_id") and e["id"] != query["event_id"]:
                continue
            name = one(db, "SELECT display_name FROM accounts WHERE id=?", (e["actor_id"],), required=False)
            items.append(p.AuditView(event_id=e["id"], event_type=e["event_type"], occurred_at=e["occurred_at"],
                actor=dict(kind=e["actor_kind"], display=name["display_name"] if name else e["actor_kind"]),
                operator_provenance=json.loads(e["operator_provenance_json"]) if e["operator_provenance_json"] else None,
                verification_method=e["verification_method"], reason_code=e["reason_code"],
                summary_code=e["summary_code"], refs=json.loads(e["refs_json"])).model_dump(mode="json"))
        return page(items, actor, route, query, secret)
    staff(actor, {"manager"})
    if route.startswith("staff/accounts"):
        if route != "staff/accounts":
            parts = route.split("/")
            account_id = parts[2]
            if len(parts) == 5 and parts[3] == "codes":
                code = one(db, "SELECT id,purpose,state,issued_at,expires_at FROM auth_codes WHERE id=? AND account_id=?", (parts[4], account_id))
                return dict(code_id=code["id"], **{key: code[key] for key in ("purpose", "state", "issued_at", "expires_at")})
            require(len(parts) == 3, "NOT_FOUND")
            return views.account(db, account_id, now)
        require(query.get("state", "active") in {"invited", "active", "suspended", "closed"}, "INVALID_REQUEST")
        return page([views.account(db, r["id"], now) for r in rows(db, "SELECT id FROM accounts WHERE state=? ORDER BY created_at,id",
                    (query.get("state", "active"),))], actor, route, query, secret)
    if route.startswith("staff/qc-profiles"):
        if route != "staff/qc-profiles":
            id_ = route.split("/")[-1]
            return p.QCProfileDetail(profile=views.profile(db, id_), history=[
                views.profile(db, id_, r["version"]) for r in rows(db,
                "SELECT version FROM qc_profile_versions WHERE profile_id=? ORDER BY version", (id_,))]).model_dump(mode="json")
        return page([views.profile(db, r["id"]) for r in rows(db, "SELECT id FROM qc_profiles ORDER BY label,id")],
                    actor, route, query, secret)
    if route == "staff/policy":
        policy = one(db, "SELECT * FROM shop_policy")
        return p.DemoPolicyView(**{key: bool(policy[key]) if key == "tax_enabled" else policy[key]
            for key in ("policy_rev", "shop_timezone", "estimate_window_days", "diagnostic_fee_minor", "currency", "tax_enabled", "tax_label")}).model_dump(mode="json")
    raise Rejected("NOT_FOUND")
