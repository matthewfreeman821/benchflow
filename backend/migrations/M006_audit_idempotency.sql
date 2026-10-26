CREATE TABLE audit_events (
    id TEXT PRIMARY KEY,
    aggregate_type TEXT NOT NULL CHECK (aggregate_type IN ('job', 'account', 'customer', 'part', 'qc_profile', 'return_case', 'contact_change_request', 'shop')),
    aggregate_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_version INTEGER NOT NULL DEFAULT 1 CHECK (event_version >= 1),
    command_id TEXT,
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('staff', 'customer', 'operator', 'system')),
    actor_id TEXT,
    operator_provenance_json TEXT CHECK (operator_provenance_json IS NULL OR json_valid(operator_provenance_json)),
    verification_method TEXT CHECK (verification_method IN ('in_person', 'phone')),
    occurred_at TEXT NOT NULL,
    reason_code TEXT,
    summary_code TEXT NOT NULL,
    refs_json TEXT NOT NULL CHECK (json_valid(refs_json))
);
CREATE INDEX events_aggregate ON audit_events(aggregate_id, occurred_at, id);
CREATE INDEX events_command ON audit_events(command_id);
CREATE TABLE idempotency_results (
    command_id TEXT PRIMARY KEY,
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('staff', 'customer')),
    actor_id TEXT NOT NULL REFERENCES accounts(id),
    request_hash TEXT NOT NULL CHECK (length(request_hash) = 64),
    response_json TEXT NOT NULL CHECK (json_valid(response_json)),
    original_http_status INTEGER NOT NULL CHECK (original_http_status = 200),
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    event_id TEXT NOT NULL REFERENCES audit_events(id)
);
CREATE INDEX idempotency_actor ON idempotency_results(actor_id, created_at);
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT, 'immutable_audit'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_events BEGIN SELECT RAISE(ABORT, 'immutable_audit'); END;
CREATE TRIGGER idempotency_no_update BEFORE UPDATE ON idempotency_results BEGIN SELECT RAISE(ABORT, 'immutable_idempotency'); END;
CREATE TRIGGER idempotency_no_delete BEFORE DELETE ON idempotency_results BEGIN SELECT RAISE(ABORT, 'immutable_idempotency'); END;
