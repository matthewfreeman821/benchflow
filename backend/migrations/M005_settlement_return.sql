CREATE TABLE settlement_entries (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    bucket TEXT NOT NULL CHECK (bucket IN ('charge', 'credit', 'payment', 'refund')),
    amount_minor INTEGER NOT NULL CHECK (typeof(amount_minor) = 'integer' AND amount_minor BETWEEN 0 AND 10000000000),
    currency TEXT NOT NULL CHECK (currency = 'USD'),
    estimate_id TEXT REFERENCES estimates(id),
    line_id TEXT REFERENCES estimate_lines(id),
    permission_id TEXT REFERENCES diagnostic_permissions(id),
    payment_id TEXT REFERENCES settlement_entries(id),
    charge_id TEXT REFERENCES settlement_entries(id),
    credit_kind TEXT CHECK (credit_kind IN ('correction', 'write_off', 'customer_credit')),
    method TEXT CHECK (method IN ('cash', 'card_external', 'bank_external', 'other_external')),
    external_reference TEXT,
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    authorized_at TEXT,
    actor_staff_id TEXT NOT NULL REFERENCES accounts(id),
    reason_code TEXT,
    CHECK (bucket != 'charge' OR ((estimate_id IS NOT NULL AND line_id IS NOT NULL) OR permission_id IS NOT NULL)),
    CHECK (bucket != 'refund' OR payment_id IS NOT NULL),
    CHECK (bucket != 'credit' OR credit_kind IS NOT NULL),
    CHECK (bucket != 'payment' OR method IS NOT NULL)
);
CREATE INDEX settlement_job ON settlement_entries(job_id, bucket, id);
CREATE INDEX settlement_payment ON settlement_entries(payment_id);
CREATE TABLE settlement_corrections (
    id TEXT PRIMARY KEY,
    entry_id TEXT NOT NULL REFERENCES settlement_entries(id),
    job_id TEXT NOT NULL REFERENCES jobs(id),
    bucket TEXT NOT NULL CHECK (bucket IN ('charge', 'credit', 'payment', 'refund')),
    delta_minor INTEGER NOT NULL CHECK (typeof(delta_minor) = 'integer' AND delta_minor != 0 AND delta_minor BETWEEN -10000000000 AND 10000000000),
    currency TEXT NOT NULL CHECK (currency = 'USD'),
    external_reference TEXT,
    reason_code TEXT NOT NULL,
    posted_at TEXT NOT NULL,
    actor_staff_id TEXT NOT NULL REFERENCES accounts(id)
);
CREATE INDEX corrections_entry ON settlement_corrections(entry_id);
CREATE TABLE collector_authorizations (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    collector_name TEXT NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('phone', 'in_person')),
    verified_by_staff_id TEXT NOT NULL REFERENCES accounts(id),
    verified_at TEXT NOT NULL,
    reason_code TEXT
);
CREATE TABLE handovers (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
    kind TEXT NOT NULL CHECK (kind IN ('completed_repair', 'return_unrepaired')),
    collector_name TEXT NOT NULL,
    collector_authorization_id TEXT REFERENCES collector_authorizations(id),
    acknowledgement TEXT NOT NULL CHECK (length(acknowledgement) > 0),
    recorded_at TEXT NOT NULL,
    actor_staff_id TEXT NOT NULL REFERENCES accounts(id),
    balance_override_reason TEXT,
    balance_minor_at_handover INTEGER NOT NULL CHECK (typeof(balance_minor_at_handover) = 'integer')
);
CREATE TABLE return_cases (
    id TEXT PRIMARY KEY,
    original_job_id TEXT NOT NULL REFERENCES jobs(id),
    return_job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
    return_rev INTEGER NOT NULL DEFAULT 1 CHECK (return_rev >= 1),
    reported_recurrence TEXT NOT NULL,
    customer_attribution TEXT,
    state TEXT NOT NULL CHECK (state IN ('open', 'closed')),
    created_at TEXT NOT NULL,
    created_by_account_id TEXT NOT NULL REFERENCES accounts(id),
    CHECK (original_job_id != return_job_id)
);
CREATE INDEX returns_original ON return_cases(original_job_id, id);
CREATE TABLE return_assessments (
    id TEXT PRIMARY KEY,
    return_case_id TEXT NOT NULL REFERENCES return_cases(id),
    inspection_permission_id TEXT NOT NULL REFERENCES diagnostic_permissions(id),
    coverage TEXT NOT NULL CHECK (coverage IN ('covered', 'not_covered', 'undetermined')),
    reason_code TEXT NOT NULL,
    actor_staff_id TEXT NOT NULL REFERENCES accounts(id),
    recorded_at TEXT NOT NULL
);
CREATE TABLE shop_policy (
    id TEXT PRIMARY KEY,
    policy_rev INTEGER NOT NULL DEFAULT 1 CHECK (policy_rev >= 1),
    shop_timezone TEXT NOT NULL CHECK (shop_timezone = 'America/New_York'),
    estimate_window_days INTEGER NOT NULL CHECK (estimate_window_days BETWEEN 1 AND 30),
    diagnostic_fee_minor INTEGER NOT NULL CHECK (typeof(diagnostic_fee_minor) = 'integer' AND diagnostic_fee_minor BETWEEN 0 AND 10000000000),
    currency TEXT NOT NULL CHECK (currency = 'USD'),
    tax_enabled INTEGER NOT NULL CHECK (tax_enabled IN (0, 1)),
    tax_label TEXT NOT NULL
);
CREATE TRIGGER entries_no_update BEFORE UPDATE ON settlement_entries BEGIN SELECT RAISE(ABORT, 'immutable_money'); END;
CREATE TRIGGER entries_no_delete BEFORE DELETE ON settlement_entries BEGIN SELECT RAISE(ABORT, 'immutable_money'); END;
CREATE TRIGGER corrections_no_update BEFORE UPDATE ON settlement_corrections BEGIN SELECT RAISE(ABORT, 'immutable_correction'); END;
CREATE TRIGGER corrections_no_delete BEFORE DELETE ON settlement_corrections BEGIN SELECT RAISE(ABORT, 'immutable_correction'); END;
CREATE TRIGGER handovers_no_update BEFORE UPDATE ON handovers BEGIN SELECT RAISE(ABORT, 'immutable_handover'); END;
CREATE TRIGGER handovers_no_delete BEFORE DELETE ON handovers BEGIN SELECT RAISE(ABORT, 'immutable_handover'); END;
