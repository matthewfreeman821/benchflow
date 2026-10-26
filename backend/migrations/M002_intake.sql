CREATE TABLE customers (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 2000),
    name_normalized TEXT NOT NULL,
    phone_value TEXT,
    phone_known INTEGER NOT NULL CHECK (phone_known IN (0, 1)),
    phone_normalized TEXT,
    email_value TEXT,
    email_known INTEGER NOT NULL CHECK (email_known IN (0, 1)),
    email_normalized TEXT,
    notes TEXT,
    customer_rev INTEGER NOT NULL DEFAULT 1 CHECK (customer_rev >= 1),
    merged_into_id TEXT REFERENCES customers(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (phone_known = 1 OR phone_value IS NULL),
    CHECK (email_known = 1 OR email_value IS NULL),
    CHECK (merged_into_id IS NULL OR merged_into_id != id)
);
CREATE INDEX customers_name ON customers(name_normalized);
CREATE INDEX customers_phone ON customers(phone_normalized);
CREATE INDEX customers_email ON customers(email_normalized);
CREATE TABLE customer_accounts (
    customer_id TEXT PRIMARY KEY REFERENCES customers(id),
    account_id TEXT NOT NULL UNIQUE REFERENCES accounts(id),
    verified_at TEXT NOT NULL,
    verified_by_staff_id TEXT NOT NULL REFERENCES accounts(id),
    verification_method TEXT NOT NULL CHECK (verification_method IN ('in_person', 'phone'))
);
CREATE TRIGGER customer_accounts_kind BEFORE INSERT ON customer_accounts
WHEN (SELECT kind FROM accounts WHERE id = NEW.account_id) != 'customer'
BEGIN SELECT RAISE(ABORT, 'customer_account_kind'); END;
CREATE TABLE customer_aliases (
    source_customer_id TEXT PRIMARY KEY REFERENCES customers(id),
    target_customer_id TEXT NOT NULL REFERENCES customers(id),
    merged_at TEXT NOT NULL,
    merged_by_staff_id TEXT NOT NULL REFERENCES accounts(id),
    verification_method TEXT NOT NULL CHECK (verification_method IN ('in_person', 'phone')),
    CHECK (source_customer_id != target_customer_id)
);
CREATE TABLE devices (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    make_value TEXT,
    make_known INTEGER NOT NULL CHECK (make_known IN (0, 1)),
    model_value TEXT,
    model_known INTEGER NOT NULL CHECK (model_known IN (0, 1)),
    serial_value TEXT,
    serial_known INTEGER NOT NULL CHECK (serial_known IN (0, 1)),
    search_normalized TEXT NOT NULL,
    CHECK (make_known = 1 OR make_value IS NULL),
    CHECK (model_known = 1 OR model_value IS NULL),
    CHECK (serial_known = 1 OR serial_value IS NULL)
);
CREATE INDEX devices_search ON devices(search_normalized);
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    job_code TEXT NOT NULL UNIQUE,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    device_id TEXT NOT NULL REFERENCES devices(id),
    lifecycle TEXT NOT NULL DEFAULT 'open' CHECK (lifecycle IN ('open', 'cancelled', 'closed')),
    job_rev INTEGER NOT NULL DEFAULT 1 CHECK (job_rev >= 1),
    gate_rev INTEGER NOT NULL DEFAULT 1 CHECK (gate_rev >= 1),
    owner_staff_id TEXT REFERENCES accounts(id),
    owner_queue TEXT CHECK (owner_queue = 'manager_intake'),
    next_action TEXT NOT NULL,
    next_action_due_at TEXT NOT NULL,
    reported_issue TEXT NOT NULL,
    condition_value TEXT,
    condition_known INTEGER NOT NULL CHECK (condition_known IN (0, 1)),
    accessories_known INTEGER NOT NULL CHECK (accessories_known IN (0, 1)),
    testing_constraints_value TEXT,
    testing_constraints_known INTEGER NOT NULL CHECK (testing_constraints_known IN (0, 1)),
    received_by_customer_name TEXT NOT NULL,
    received_by_staff_id TEXT REFERENCES accounts(id),
    acknowledgement_version TEXT NOT NULL,
    acknowledgement_method TEXT NOT NULL CHECK (acknowledgement_method IN ('in_person', 'phone')),
    acknowledgement_actor_id TEXT NOT NULL REFERENCES accounts(id),
    opened_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    disposition TEXT CHECK (disposition IN ('cancelled', 'no_fault_found', 'unrepairable')),
    repair_hold TEXT CHECK (repair_hold IN ('revision', 'revoked', 'cancelled', 'safety')),
    safe_stop_required INTEGER NOT NULL DEFAULT 0 CHECK (safe_stop_required IN (0, 1)),
    CHECK ((owner_staff_id IS NULL) != (owner_queue IS NULL)),
    CHECK (condition_known = 1 OR condition_value IS NULL),
    CHECK (testing_constraints_known = 1 OR testing_constraints_value IS NULL)
);
CREATE INDEX jobs_customer ON jobs(customer_id, opened_at, id);
CREATE INDEX jobs_queue ON jobs(lifecycle, next_action_due_at, updated_at, id);
CREATE TABLE job_accessories (
    job_id TEXT NOT NULL REFERENCES jobs(id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    label TEXT NOT NULL,
    PRIMARY KEY (job_id, ordinal)
);
CREATE TABLE job_assignments (
    job_id TEXT NOT NULL REFERENCES jobs(id),
    staff_id TEXT NOT NULL REFERENCES accounts(id),
    PRIMARY KEY (job_id, staff_id)
);
CREATE INDEX assignments_staff ON job_assignments(staff_id, job_id);
CREATE TABLE follow_ups (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    state TEXT NOT NULL CHECK (state IN ('due', 'resolved')),
    owner_staff_id TEXT REFERENCES accounts(id),
    owner_queue TEXT CHECK (owner_queue = 'manager_intake'),
    next_action TEXT NOT NULL,
    due_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    CHECK ((owner_staff_id IS NULL) != (owner_queue IS NULL)),
    CHECK ((state = 'resolved') = (resolved_at IS NOT NULL))
);
CREATE INDEX follow_ups_due ON follow_ups(state, due_at, job_id);
CREATE TABLE contact_attempts (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    channel TEXT NOT NULL CHECK (channel IN ('phone', 'in_person', 'other_manual')),
    outcome TEXT NOT NULL CHECK (outcome IN ('no_answer', 'message_left', 'spoke', 'customer_declined', 'callback_requested')),
    summary TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    actor_staff_id TEXT NOT NULL REFERENCES accounts(id)
);
CREATE TABLE contact_change_requests (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    account_id TEXT NOT NULL REFERENCES accounts(id),
    request_rev INTEGER NOT NULL DEFAULT 1 CHECK (request_rev >= 1),
    state TEXT NOT NULL CHECK (state IN ('pending', 'accepted', 'rejected')),
    requested_fields_json TEXT NOT NULL CHECK (json_valid(requested_fields_json)),
    proposed_values_json TEXT NOT NULL CHECK (json_valid(proposed_values_json)),
    requested_at TEXT NOT NULL,
    resolved_at TEXT,
    resolved_by_staff_id TEXT REFERENCES accounts(id),
    confirmation_method TEXT CHECK (confirmation_method IN ('in_person', 'phone')),
    reason_code TEXT,
    CHECK ((state = 'pending') = (resolved_at IS NULL))
);
CREATE INDEX contact_requests_owner ON contact_change_requests(customer_id, requested_at, id);
CREATE INDEX contact_requests_queue ON contact_change_requests(state, requested_at, id);
