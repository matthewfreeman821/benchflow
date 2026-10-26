"""Server-side authentication. All state transitions run in the caller's transaction."""
from __future__ import annotations

import hmac

from backend.core import (
    Rejected, account_actor, event, insert, instant, later, one, require, uid,
)
from backend.credentials import fingerprint, hash_password, new_secret, normalize_login, verify_password


def csrf_for(token):
    # A pseudorandom session-bound token, reconstructible only with the opaque cookie.
    return fingerprint("benchflow-csrf:" + token)


def issue_session(db, now, account=None):
    token = new_secret()
    csrf = csrf_for(token)
    customer = account is not None and account["kind"] == "customer"
    idle = 60 if customer else 30
    absolute = 24 if customer else 12
    insert(db, "sessions", token_hash=fingerprint(token), csrf_hash=fingerprint(csrf),
           account_id=account["id"] if account else None,
           auth_rev=account["auth_rev"] if account else None, created_at=now, last_seen_at=now,
           idle_expires_at=later(now, minutes=idle if account else 10),
           absolute_expires_at=later(now, hours=absolute) if account else later(now, minutes=10))
    return token, csrf


def validate_session(db, token, now, *, csrf=None, authenticated=True, touch=True):
    session = one(db, "SELECT * FROM sessions WHERE token_hash=?", (fingerprint(token or ""),), required=False)
    require(session is not None, "AUTH_REQUIRED" if authenticated else "CSRF_INVALID")
    if session["revocation_reason"]:
        raise Rejected("FORBIDDEN" if session["revocation_reason"] == "roles_changed" else "AUTH_REQUIRED")
    require(instant(now) < instant(session["idle_expires_at"]) and
            instant(now) < instant(session["absolute_expires_at"]), "AUTH_REQUIRED")
    if csrf is not None:
        require(hmac.compare_digest(session["csrf_hash"], fingerprint(csrf)), "CSRF_INVALID")
    actor = None
    if session["account_id"]:
        actor = account_actor(db, session["account_id"])
        require(actor["state"] == "active" and actor["credential_state"] == "usable" and
                actor["auth_rev"] == session["auth_rev"], "AUTH_REQUIRED")
        if actor["kind"] == "customer":
            require(actor["customer_id"] is not None, "AUTH_REQUIRED")
    require(not authenticated or actor is not None, "AUTH_REQUIRED")
    if touch:
        idle = 60 if actor and actor["kind"] == "customer" else 30 if actor else 10
        new_idle = min(instant(later(now, minutes=idle)), instant(session["absolute_expires_at"]))
        from backend.core import stamp
        db.execute("UPDATE sessions SET last_seen_at=?, idle_expires_at=? WHERE token_hash=?",
                   (now, stamp(new_idle), session["token_hash"]))
        session["idle_expires_at"] = stamp(new_idle)
    return actor, session


def revoke(db, account_id, reason):
    db.execute("UPDATE sessions SET revocation_reason=? WHERE account_id=?", (reason, account_id))
    db.execute("UPDATE accounts SET auth_rev=auth_rev+1,account_rev=account_rev+1 WHERE id=?", (account_id,))
    db.execute("UPDATE auth_codes SET state='revoked' WHERE account_id=? AND state='pending'", (account_id,))
    db.execute("UPDATE invitations SET state='revoked' WHERE account_id=? AND state='pending'", (account_id,))


def throttle(db, subject, peer, purpose, now, *, failed=False, clear=False):
    # Separate per-subject/per-peer and per-peer buckets. Ignore all forwarded headers.
    for key in (fingerprint(subject), fingerprint("*shared-peer*")):
        args = (key, fingerprint(peer), purpose)
        record = one(db, "SELECT * FROM auth_throttles WHERE subject_hash=? AND peer_hash=? AND purpose=?",
                     args, required=False)
        if clear:
            db.execute("DELETE FROM auth_throttles WHERE subject_hash=? AND peer_hash=? AND purpose=?", args)
            continue
        if record and record["blocked_until"] and instant(now) < instant(record["blocked_until"]):
            raise Rejected("RATE_LIMITED")
        if not failed:
            continue
        failures = (record["failures"] if record and
                    instant(now) < instant(later(record["window_started_at"], minutes=15)) else 0) + 1
        # Shared peer gets a wider ceiling to avoid one miss blocking all local users.
        threshold = 20 if key == fingerprint("*shared-peer*") else 5
        delay = min(900, 2 ** min(10, failures - threshold + 1)) if purpose == "login" else 900
        blocked = later(now, seconds=delay) if failures >= threshold else None
        db.execute(
            "INSERT INTO auth_throttles VALUES (?,?,?,?,?,?) ON CONFLICT(subject_hash,peer_hash,purpose) "
            "DO UPDATE SET failures=excluded.failures,window_started_at=excluded.window_started_at,"
            "blocked_until=excluded.blocked_until",
            (*args, failures, record["window_started_at"] if record and failures > 1 else now, blocked))


def authenticate(db, data, peer, now):
    normalized = normalize_login(data.login_id)
    throttle(db, normalized, peer, "login", now)
    account = one(db, "SELECT id,password_hash FROM accounts WHERE login_normalized=?",
                  (normalized,), required=False)
    # Equal Argon2 verification workload for nonexistent/unusable identities.
    dummy = _dummy_hash()
    valid = verify_password(account["password_hash"] if account and account["password_hash"] else dummy, data.password)
    actor = account_actor(db, account["id"]) if account else None
    if not valid or actor["state"] != "active" or actor["credential_state"] != "usable":
        throttle(db, normalized, peer, "login", now, failed=True)
        event(db, {"kind": "system", "id": None}, "shop", _AUTH_SHOP,
              "auth.login_failed", now)
        return None
    throttle(db, normalized, peer, "login", now, clear=True)
    event(db, actor, "account", actor["id"], "auth.login_succeeded", now)
    return actor


_DUMMY = None
_AUTH_SHOP = "ab71d503-62dc-4428-8c4a-131827df60ce"


def _dummy_hash():
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password(new_secret())
    return _DUMMY


def redeem(db, data, purpose, peer, now):
    digest = fingerprint(data.code)
    throttle(db, digest, peer, purpose, now)
    code = one(db, "SELECT * FROM auth_codes WHERE token_hash=?", (digest,), required=False)
    account = account_actor(db, code["account_id"]) if code else None
    valid = code and code["state"] == "pending" and instant(now) < instant(code["expires_at"])
    valid = valid and code["purpose"] == ("invite" if purpose == "activate" else "recovery")
    valid = valid and account["state"] == ("invited" if purpose == "activate" else "active")
    invite = one(db, "SELECT * FROM invitations WHERE id=?", (code["invitation_id"],), required=False) if code else None
    if purpose == "activate":
        valid = valid and invite and invite["state"] == "pending" and instant(now) < instant(invite["expires_at"])
    if not valid:
        throttle(db, digest, peer, purpose, now, failed=True)
        return False
    if purpose == "activate":
        normalized = normalize_login(data.login_id)
        require(one(db, "SELECT 1 FROM accounts WHERE login_normalized=? AND id!=?",
                    (normalized, account["id"]), required=False) is None, "DUPLICATE_LOGIN")
        db.execute("UPDATE accounts SET login_id=?,login_normalized=?,state='active' WHERE id=?",
                   (data.login_id, normalized, account["id"]))
    encoded = hash_password(data.password if purpose == "activate" else data.new_password)
    revoke(db, account["id"], "recovered")
    db.execute("UPDATE accounts SET password_hash=?,credential_state='usable',updated_at=? WHERE id=?",
               (encoded, now, account["id"]))
    db.execute("UPDATE auth_codes SET state='redeemed',redeemed_at=? WHERE id=?", (now, code["id"]))
    if invite:
        db.execute("UPDATE invitations SET state='redeemed' WHERE id=?", (invite["id"],))
    throttle(db, digest, peer, purpose, now, clear=True)
    event(db, account, "account", account["id"],
          "auth.invite_redeemed" if purpose == "activate" else "auth.recovery_redeemed", now)
    return True
