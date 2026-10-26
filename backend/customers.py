"""Customer, account and verified ownership transitions."""
from __future__ import annotations

import json
import unicodedata

from backend.core import (
    account_actor, eligible_staff, encode, insert, instant, later, one, require,
    revision, rows, uid,
)
from backend.credentials import fingerprint, new_secret
from backend.identity import revoke


def normalized(value):
    return unicodedata.normalize("NFKC", value or "").casefold().strip()


def normalized_phone(value):
    text = normalized(value)
    digits = "".join(c for c in text if c.isdecimal())
    return ("+" if text.startswith("+") else "") + digits if digits else None


def customer_values(data):
    return dict(
        display_name=data["display_name"], name_normalized=" ".join(normalized(data["display_name"]).split()),
        phone_value=data["phone"]["value"], phone_known=int(data["phone"]["known"]),
        phone_normalized=normalized_phone(data["phone"]["value"]),
        email_value=data["email"]["value"], email_known=int(data["email"]["known"]),
        email_normalized=normalized(data["email"]["value"]) or None,
    )


def update_customer(db, customer_id, data, now):
    values = customer_values(data)
    db.execute("UPDATE customers SET " + ",".join(f"{key}=?" for key in values) +
               ",customer_rev=customer_rev+1,updated_at=? WHERE id=?", (*values.values(), now, customer_id))


def issue_code(db, actor, account, now, *, purpose, owner=None, due=None, method=None, note=None):
    code, code_id = new_secret(), uid()
    expires = later(now, hours=72 if purpose == "invite" else 24)
    db.execute("UPDATE auth_codes SET state='superseded' WHERE account_id=? AND purpose=? AND state='pending'",
               (account["id"], purpose))
    invitation_id = None
    if purpose == "invite":
        db.execute("UPDATE invitations SET state='superseded' WHERE account_id=? AND state='pending'", (account["id"],))
        eligible_staff(db, owner)
        require(instant(now) < instant(due) < instant(expires))
        invitation_id = insert(db, "invitations", id=uid(), account_id=account["id"], state="pending",
                               owner_staff_id=owner, next_action_due_at=due, verification_method=method,
                               verified_by_staff_id=actor["id"], issued_at=now, expires_at=expires,
                               contact_handoff_note=note)
    insert(db, "auth_codes", id=code_id, token_hash=fingerprint(code), account_id=account["id"],
           invitation_id=invitation_id, purpose=purpose, state="pending", issued_at=now, expires_at=expires,
           issued_by_staff_id=actor["id"], verification_method=method)
    return invitation_id or code_id, dict(value=code, label="NOT SENT — give in person or read out by phone",
                                         expires_at=expires, shown_once=True)


def last_manager(db, account, *, roles=None):
    if account["state"] == "active" and "manager" in account["roles"] and (roles is None or "manager" not in roles):
        count = one(db, "SELECT count(*) AS n FROM accounts a JOIN staff_roles r ON a.id=r.account_id "
                    "WHERE a.state='active' AND r.role='manager'")["n"]
        require(count > 1, "LAST_MANAGER")


def handle(ctx):
    db, a, d, actor, now = ctx.db, ctx.action, ctx.data, ctx.actor, ctx.now
    if a == "customer.create":
        id_ = insert(db, "customers", id=uid(), **customer_values(d), notes=d["notes"], created_at=now, updated_at=now)
        return ctx.result("customer", id_, "customer", id_)
    if a == "customer.update_contact":
        customer = one(db, "SELECT * FROM customers WHERE id=? AND merged_into_id IS NULL", (ctx.target,))
        revision(ctx.expected, "customer_rev", customer["customer_rev"])
        require(d["display_name"] != customer["display_name"] or
                d["phone"] != dict(value=customer["phone_value"], known=bool(customer["phone_known"])) or
                d["email"] != dict(value=customer["email_value"], known=bool(customer["email_known"])))
        update_customer(db, customer["id"], d, now)
        return ctx.result("customer", customer["id"], "customer", customer["id"])
    if a == "customer.merge":
        source = one(db, "SELECT * FROM customers WHERE id=? AND merged_into_id IS NULL", (d["source_customer_id"],))
        target = one(db, "SELECT * FROM customers WHERE id=? AND merged_into_id IS NULL", (d["target_customer_id"],))
        revision(ctx.expected, "source_customer_rev", source["customer_rev"])
        revision(ctx.expected, "target_customer_rev", target["customer_rev"])
        source_link = one(db, "SELECT * FROM customer_accounts WHERE customer_id=?", (source["id"],), required=False)
        target_link = one(db, "SELECT * FROM customer_accounts WHERE customer_id=?", (target["id"],), required=False)
        for link in (source_link, target_link):
            if link:
                revoke(db, link["account_id"], "ownership_changed")
        if source_link:
            db.execute("DELETE FROM customer_accounts WHERE customer_id=?", (source["id"],))
            if target_link:
                db.execute("UPDATE accounts SET state='closed',password_hash=NULL,credential_state='reset_required' WHERE id=?",
                           (source_link["account_id"],))
            else:
                insert(db, "customer_accounts", customer_id=target["id"], account_id=source_link["account_id"],
                       verified_at=now, verified_by_staff_id=actor["id"], verification_method=d["verification_method"])
        db.execute("UPDATE jobs SET customer_id=?,job_rev=job_rev+1,gate_rev=gate_rev+1,updated_at=? WHERE customer_id=?",
                   (target["id"], now, source["id"]))
        db.execute("UPDATE devices SET customer_id=? WHERE customer_id=?", (target["id"], source["id"]))
        # Keep source contact requests attached to their historical customer, never silently transfer private requests.
        db.execute("UPDATE customers SET merged_into_id=?,customer_rev=customer_rev+1,updated_at=? WHERE id=?",
                   (target["id"], now, source["id"]))
        db.execute("UPDATE customers SET customer_rev=customer_rev+1,updated_at=? WHERE id=?", (now, target["id"]))
        insert(db, "customer_aliases", source_customer_id=source["id"], target_customer_id=target["id"],
               merged_at=now, merged_by_staff_id=actor["id"], verification_method=d["verification_method"])
        return ctx.result("customer", target["id"], "customer", target["id"])
    if a in {"account.invite_customer", "account.invite_staff"}:
        customer = None
        if a.endswith("customer"):
            customer = one(db, "SELECT * FROM customers WHERE id=? AND merged_into_id IS NULL", (d["customer_id"],))
            revision(ctx.expected, "customer_rev", customer["customer_rev"])
            require(one(db, "SELECT 1 FROM customer_accounts WHERE customer_id=?", (customer["id"],), required=False) is None)
        account_id = insert(db, "accounts", id=uid(), kind="customer" if customer else "staff", state="invited",
                            display_name=customer["display_name"] if customer else d["display_name"],
                            credential_state="not_set", created_at=now, updated_at=now)
        if customer:
            insert(db, "customer_accounts", customer_id=customer["id"], account_id=account_id, verified_at=now,
                   verified_by_staff_id=actor["id"], verification_method=d["verification_method"])
            db.execute("UPDATE customers SET customer_rev=customer_rev+1,updated_at=? WHERE id=?", (now, customer["id"]))
        else:
            for role in d["roles"]:
                insert(db, "staff_roles", account_id=account_id, role=role)
        account = account_actor(db, account_id)
        code_id, code = issue_code(db, actor, account, now, purpose="invite", owner=d["owner_staff_id"],
                                   due=d["next_action_due_at"], method=d.get("verification_method"),
                                   note=d.get("contact_handoff_note"))
        return ctx.result("account", account_id, "invite", code_id, code=code)
    if a.startswith("account."):
        account = account_actor(db, d["account_id"])
        revision(ctx.expected, "account_rev", account["account_rev"])
        if "manager" not in actor["roles"]:
            require(account["kind"] == "customer", "FORBIDDEN")
        if a in {"account.change_roles", "account.suspend", "account.close", "account.unlink_customer"}:
            require(account["id"] != actor["id"], "FORBIDDEN")
            last_manager(db, account, roles=d.get("roles"))
        if a == "account.reissue_invite":
            require(account["state"] == "invited")
            old = one(db, "SELECT * FROM invitations WHERE account_id=? ORDER BY issued_at DESC,rowid DESC LIMIT 1", (account["id"],))
            code_id, code = issue_code(db, actor, account, now, purpose="invite", owner=old["owner_staff_id"],
                                       due=later(now, hours=48), method=old["verification_method"])
            db.execute("UPDATE accounts SET account_rev=account_rev+1 WHERE id=?", (account["id"],))
            return ctx.result("account", account["id"], "invite", code_id, code=code)
        if a == "account.issue_recovery":
            require(account["state"] == "active")
            require(account["id"] != actor["id"], "FORBIDDEN")
            if account["kind"] == "customer":
                require(d["verification_method"] in {"in_person", "phone"})
            code_id, code = issue_code(db, actor, account, now, purpose="recovery", method=d["verification_method"])
            db.execute("UPDATE accounts SET account_rev=account_rev+1 WHERE id=?", (account["id"],))
            return ctx.result("account", account["id"], "auth_code", code_id, code=code)
        if a == "account.change_roles":
            require(account["kind"] == "staff" and account["state"] != "closed")
            db.execute("DELETE FROM staff_roles WHERE account_id=?", (account["id"],))
            for role in d["roles"]:
                insert(db, "staff_roles", account_id=account["id"], role=role)
            revoke(db, account["id"], "roles_changed")
        elif a == "account.revoke_invite":
            require(account["state"] == "invited")
            invite = one(db, "SELECT id FROM invitations WHERE account_id=? ORDER BY rowid DESC LIMIT 1", (account["id"],))
            db.execute("UPDATE auth_codes SET state='revoked' WHERE account_id=? AND state='pending'", (account["id"],))
            db.execute("UPDATE invitations SET state='revoked' WHERE account_id=? AND state='pending'", (account["id"],))
            db.execute("UPDATE accounts SET account_rev=account_rev+1 WHERE id=?", (account["id"],))
            return ctx.result("account", account["id"], "invite", invite["id"])
        elif a == "account.reactivate":
            require(account["state"] == "suspended")
            db.execute("UPDATE accounts SET state='active',account_rev=account_rev+1,auth_rev=auth_rev+1 WHERE id=?", (account["id"],))
        elif a in {"account.suspend", "account.close", "account.unlink_customer"}:
            require(account["state"] != "closed")
            if a == "account.unlink_customer":
                require(account["kind"] == "customer" and account["customer_id"] == d["customer_id"], "NOT_FOUND")
                customer = one(db, "SELECT customer_rev FROM customers WHERE id=?", (d["customer_id"],))
                revision(ctx.expected, "customer_rev", customer["customer_rev"])
            revoke(db, account["id"], "suspended" if a.endswith("suspend") else "closed")
            db.execute("UPDATE accounts SET state=?,updated_at=? WHERE id=?",
                       ("suspended" if a.endswith("suspend") else "closed", now, account["id"]))
            if account["kind"] == "staff":
                db.execute("UPDATE jobs SET owner_staff_id=NULL,owner_queue='manager_intake',job_rev=job_rev+1,"
                           "next_action='Reassign follow-up owner',updated_at=? WHERE owner_staff_id=? AND lifecycle!='closed'",
                           (now, account["id"]))
            if a in {"account.close", "account.unlink_customer"} and account["customer_id"]:
                db.execute("DELETE FROM customer_accounts WHERE account_id=?", (account["id"],))
                db.execute("UPDATE customers SET customer_rev=customer_rev+1,updated_at=? WHERE id=?", (now, account["customer_id"]))
        return ctx.result("account", account["id"], "account", account["id"])
    if a == "contact.change_request":
        require(actor["kind"] == "customer", "FORBIDDEN")
        c = one(db, "SELECT * FROM customers WHERE id=?", (actor["customer_id"],))
        current = dict(display_name=dict(value=c["display_name"], known=True),
                       phone=dict(value=c["phone_value"], known=bool(c["phone_known"])),
                       email=dict(value=c["email_value"], known=bool(c["email_known"])))
        require(any(d[field] != current[field] for field in d["requested_fields"]))
        for field in ("display_name", "phone", "email"):
            if field not in d["requested_fields"]:
                require(d[field] == {"known": False, "value": None})
        id_ = insert(db, "contact_change_requests", id=uid(), customer_id=actor["customer_id"], account_id=actor["id"],
                     state="pending", requested_fields_json=encode(d["requested_fields"]),
                     proposed_values_json=encode({key: d[key] for key in d["requested_fields"]}), requested_at=now)
        return ctx.result("contact_change_request", id_, "contact_change_request", id_)
    if a == "contact.change_resolve":
        request = one(db, "SELECT * FROM contact_change_requests WHERE id=?", (d["request_id"],))
        customer = one(db, "SELECT * FROM customers WHERE id=? AND merged_into_id IS NULL", (request["customer_id"],))
        revision(ctx.expected, "request_rev", request["request_rev"])
        revision(ctx.expected, "customer_rev", customer["customer_rev"])
        require(request["state"] == "pending")
        if d["decision"] == "accept":
            proposed = json.loads(request["proposed_values_json"])
            values = dict(display_name=customer["display_name"],
                          phone={"known": bool(customer["phone_known"]), "value": customer["phone_value"]},
                          email={"known": bool(customer["email_known"]), "value": customer["email_value"]})
            for key, value in proposed.items():
                if key == "display_name":
                    require(value["known"] and bool(value["value"]))
                    values[key] = value["value"]
                else:
                    values[key] = value
            update_customer(db, customer["id"], values, now)
        db.execute("UPDATE contact_change_requests SET state=?,request_rev=request_rev+1,resolved_at=?,"
                   "resolved_by_staff_id=?,confirmation_method=?,reason_code=? WHERE id=?",
                   ("accepted" if d["decision"] == "accept" else "rejected", now, actor["id"],
                    d["confirmation_method"], d["reason_code"], request["id"]))
        return ctx.result("contact_change_request", request["id"], "contact_change_request", request["id"])
    raise RuntimeError("CUSTOMER_HANDLER_MISSING")
