CREATE TABLE diagnostic_permissions (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    previous_permission_id TEXT REFERENCES diagnostic_permissions(id),
    state TEXT NOT NULL CHECK (state IN ('pending', 'granted', 'declined', 'revoked')),
    scope TEXT NOT NULL,
    fee_minor INTEGER NOT NULL CHECK (typeof(fee_minor) = 'integer' AND fee_minor BETWEEN 0 AND 10000000000),
    currency TEXT NOT NULL CHECK (currency = 'USD'),
    method TEXT NOT NULL CHECK (method IN ('customer_account', 'phone', 'in_person')),
    attribution TEXT,
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('staff', 'customer')),
    actor_id TEXT NOT NULL REFERENCES accounts(id),
    decided_at TEXT NOT NULL
);
CREATE INDEX diagnostic_permissions_job ON diagnostic_permissions(job_id, decided_at, id);
CREATE TABLE diagnostic_records (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    permission_id TEXT NOT NULL REFERENCES diagnostic_permissions(id),
    task_id TEXT REFERENCES work_tasks(id),
    kind TEXT NOT NULL CHECK (kind IN ('test', 'finding', 'limitation')),
    summary TEXT NOT NULL,
    customer_text TEXT,
    customer_visible INTEGER NOT NULL CHECK (customer_visible IN (0, 1)),
    uncertainty TEXT,
    actor_staff_id TEXT NOT NULL REFERENCES accounts(id),
    recorded_at TEXT NOT NULL,
    CHECK ((customer_visible = 1) = (customer_text IS NOT NULL))
);
CREATE TABLE estimate_drafts (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    currency TEXT NOT NULL CHECK (currency = 'USD'),
    tax_minor INTEGER NOT NULL CHECK (typeof(tax_minor) = 'integer' AND tax_minor BETWEEN 0 AND 10000000000),
    tax_label TEXT NOT NULL,
    lines_json TEXT NOT NULL CHECK (json_valid(lines_json)),
    updated_at TEXT NOT NULL,
    updated_by_staff_id TEXT NOT NULL REFERENCES accounts(id)
);
CREATE TABLE estimates (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    version INTEGER NOT NULL CHECK (version >= 1),
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    currency TEXT NOT NULL CHECK (currency = 'USD'),
    subtotal_minor INTEGER NOT NULL CHECK (typeof(subtotal_minor) = 'integer' AND subtotal_minor BETWEEN 0 AND 10000000000),
    tax_minor INTEGER NOT NULL CHECK (typeof(tax_minor) = 'integer' AND tax_minor BETWEEN 0 AND 10000000000),
    total_minor INTEGER NOT NULL CHECK (typeof(total_minor) = 'integer' AND total_minor BETWEEN 0 AND 10000000000),
    tax_label TEXT NOT NULL,
    tax_enabled INTEGER NOT NULL CHECK (tax_enabled IN (0, 1)),
    content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
    change_reason TEXT NOT NULL,
    change_json TEXT NOT NULL CHECK (json_valid(change_json)),
    issued_by_staff_id TEXT NOT NULL REFERENCES accounts(id),
    UNIQUE (job_id, version),
    CHECK (expires_at > issued_at),
    CHECK (total_minor = subtotal_minor + tax_minor)
);
CREATE TABLE estimate_lines (
    id TEXT PRIMARY KEY,
    estimate_id TEXT NOT NULL REFERENCES estimates(id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    kind TEXT NOT NULL CHECK (kind IN ('labor', 'part', 'fee', 'credit')),
    description TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 10000),
    unit_amount_minor INTEGER NOT NULL CHECK (typeof(unit_amount_minor) = 'integer' AND unit_amount_minor BETWEEN 0 AND 10000000000),
    line_amount_minor INTEGER NOT NULL CHECK (typeof(line_amount_minor) = 'integer' AND line_amount_minor BETWEEN -10000000000 AND 10000000000),
    scope_ref TEXT NOT NULL,
    UNIQUE (estimate_id, ordinal),
    CHECK (line_amount_minor = quantity * unit_amount_minor * CASE WHEN kind = 'credit' THEN -1 ELSE 1 END)
);
CREATE TABLE estimate_decisions (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    estimate_id TEXT NOT NULL REFERENCES estimates(id),
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'declined', 'revoked')),
    method TEXT NOT NULL CHECK (method IN ('customer_account', 'phone', 'in_person')),
    attribution TEXT,
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('staff', 'customer')),
    actor_id TEXT NOT NULL REFERENCES accounts(id),
    decided_at TEXT NOT NULL,
    reason_code TEXT
);
CREATE UNIQUE INDEX estimate_one_decision ON estimate_decisions(estimate_id) WHERE decision != 'revoked';
CREATE UNIQUE INDEX estimate_one_revocation ON estimate_decisions(estimate_id) WHERE decision = 'revoked';
CREATE TRIGGER estimates_no_update BEFORE UPDATE ON estimates BEGIN SELECT RAISE(ABORT, 'immutable_estimate'); END;
CREATE TRIGGER estimates_no_delete BEFORE DELETE ON estimates BEGIN SELECT RAISE(ABORT, 'immutable_estimate'); END;
CREATE TRIGGER estimate_lines_no_update BEFORE UPDATE ON estimate_lines BEGIN SELECT RAISE(ABORT, 'immutable_estimate_line'); END;
CREATE TRIGGER estimate_lines_no_delete BEFORE DELETE ON estimate_lines BEGIN SELECT RAISE(ABORT, 'immutable_estimate_line'); END;
CREATE TRIGGER decisions_no_update BEFORE UPDATE ON estimate_decisions BEGIN SELECT RAISE(ABORT, 'immutable_decision'); END;
CREATE TRIGGER decisions_no_delete BEFORE DELETE ON estimate_decisions BEGIN SELECT RAISE(ABORT, 'immutable_decision'); END;
CREATE TRIGGER permissions_no_update BEFORE UPDATE ON diagnostic_permissions BEGIN SELECT RAISE(ABORT, 'immutable_permission'); END;
CREATE TRIGGER permissions_no_delete BEFORE DELETE ON diagnostic_permissions BEGIN SELECT RAISE(ABORT, 'immutable_permission'); END;
