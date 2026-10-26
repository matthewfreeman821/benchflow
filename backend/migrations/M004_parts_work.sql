CREATE TABLE parts (
    id TEXT PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    unit TEXT NOT NULL,
    on_hand_qty INTEGER NOT NULL CHECK (typeof(on_hand_qty) = 'integer' AND on_hand_qty >= 0),
    reserved_qty INTEGER NOT NULL CHECK (typeof(reserved_qty) = 'integer' AND reserved_qty BETWEEN 0 AND on_hand_qty),
    stock_rev INTEGER NOT NULL DEFAULT 1 CHECK (stock_rev >= 1)
);
CREATE TABLE part_requirements (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    part_id TEXT NOT NULL REFERENCES parts(id),
    scope_ref TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK (qty BETWEEN 1 AND 10000),
    needed_by TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX requirements_job ON part_requirements(job_id, part_id);
CREATE INDEX requirements_part ON part_requirements(part_id, job_id);
CREATE TABLE reservations (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    part_id TEXT NOT NULL REFERENCES parts(id),
    scope_ref TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK (qty BETWEEN 1 AND 10000),
    released_qty INTEGER NOT NULL DEFAULT 0 CHECK (released_qty >= 0),
    consumed_qty INTEGER NOT NULL DEFAULT 0 CHECK (consumed_qty >= 0),
    created_at TEXT NOT NULL,
    created_by_staff_id TEXT NOT NULL REFERENCES accounts(id),
    CHECK (released_qty + consumed_qty <= qty)
);
CREATE INDEX reservations_job ON reservations(job_id, part_id);
CREATE INDEX reservations_part ON reservations(part_id, job_id);
CREATE TABLE supplier_orders (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    part_id TEXT NOT NULL REFERENCES parts(id),
    qty INTEGER NOT NULL CHECK (qty BETWEEN 1 AND 10000),
    supplier_label TEXT NOT NULL,
    eta_at TEXT,
    eta_evidence TEXT,
    state TEXT NOT NULL CHECK (state IN ('planned', 'placed_unconfirmed', 'supplier_confirmed', 'backordered', 'received', 'cancellation_reported', 'unavailable')),
    recorded_at TEXT NOT NULL,
    actor_staff_id TEXT NOT NULL REFERENCES accounts(id)
);
CREATE TABLE supplier_order_outcomes (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES supplier_orders(id),
    outcome TEXT NOT NULL CHECK (outcome IN ('supplier_confirmed', 'backordered', 'cancellation_reported', 'unavailable')),
    evidence TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    actor_staff_id TEXT NOT NULL REFERENCES accounts(id)
);
CREATE TABLE receipts (
    id TEXT PRIMARY KEY,
    order_id TEXT REFERENCES supplier_orders(id),
    part_id TEXT NOT NULL REFERENCES parts(id),
    qty INTEGER NOT NULL CHECK (qty BETWEEN 1 AND 10000),
    source_note TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    actor_staff_id TEXT NOT NULL REFERENCES accounts(id)
);
CREATE TABLE stock_movements (
    id TEXT PRIMARY KEY,
    part_id TEXT NOT NULL REFERENCES parts(id),
    job_id TEXT REFERENCES jobs(id),
    reservation_id TEXT REFERENCES reservations(id),
    receipt_id TEXT REFERENCES receipts(id),
    task_id TEXT REFERENCES work_tasks(id),
    source_movement_id TEXT REFERENCES stock_movements(id),
    kind TEXT NOT NULL CHECK (kind IN ('opening', 'adjustment', 'receipt', 'reservation', 'release', 'consumption', 'return_to_stock')),
    on_hand_delta INTEGER NOT NULL,
    reserved_delta INTEGER NOT NULL,
    reason_code TEXT NOT NULL,
    source_note TEXT,
    actor_staff_id TEXT NOT NULL REFERENCES accounts(id),
    occurred_at TEXT NOT NULL,
    CHECK (typeof(on_hand_delta) = 'integer' AND typeof(reserved_delta) = 'integer'),
    CHECK (kind != 'reservation' OR (on_hand_delta = 0 AND reserved_delta > 0)),
    CHECK (kind != 'release' OR (on_hand_delta = 0 AND reserved_delta < 0)),
    CHECK (kind != 'consumption' OR (on_hand_delta < 0 AND reserved_delta = on_hand_delta)),
    CHECK (kind NOT IN ('receipt', 'return_to_stock') OR (on_hand_delta > 0 AND reserved_delta = 0)),
    CHECK (kind != 'return_to_stock' OR (source_movement_id IS NOT NULL AND job_id IS NOT NULL AND source_note IS NOT NULL))
);
CREATE INDEX movements_part ON stock_movements(part_id, occurred_at, id);
CREATE INDEX movements_job ON stock_movements(job_id, id);
CREATE TABLE qc_profiles (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    profile_rev INTEGER NOT NULL DEFAULT 1 CHECK (profile_rev >= 1)
);
CREATE TABLE qc_profile_versions (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES qc_profiles(id),
    version INTEGER NOT NULL CHECK (version >= 1),
    created_at TEXT NOT NULL,
    created_by_staff_id TEXT NOT NULL REFERENCES accounts(id),
    reason_code TEXT NOT NULL,
    UNIQUE (profile_id, version)
);
CREATE TABLE qc_profile_checks (
    id TEXT PRIMARY KEY,
    profile_version_id TEXT NOT NULL REFERENCES qc_profile_versions(id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    label TEXT NOT NULL,
    required INTEGER NOT NULL CHECK (required IN (0, 1)),
    UNIQUE (profile_version_id, ordinal)
);
CREATE TABLE work_tasks (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    scope_ref TEXT NOT NULL,
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('diagnostic', 'estimate_line')),
    assigned_staff_id TEXT NOT NULL REFERENCES accounts(id),
    summary TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('not_started', 'active', 'paused', 'completed', 'rework')),
    profile_version_id TEXT REFERENCES qc_profile_versions(id),
    rework_of_task_id TEXT REFERENCES work_tasks(id),
    started_under_estimate_id TEXT REFERENCES estimates(id),
    started_under_permission_id TEXT REFERENCES diagnostic_permissions(id),
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);
CREATE INDEX tasks_job ON work_tasks(job_id, state, id);
CREATE TABLE work_logs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES work_tasks(id),
    kind TEXT NOT NULL CHECK (kind IN ('start', 'note', 'pause', 'resume', 'complete')),
    summary TEXT NOT NULL,
    actor_staff_id TEXT NOT NULL REFERENCES accounts(id),
    recorded_at TEXT NOT NULL,
    ended_at TEXT,
    owner_staff_id TEXT REFERENCES accounts(id),
    next_action TEXT,
    next_action_due_at TEXT,
    CHECK ((kind = 'pause') = (owner_staff_id IS NOT NULL AND next_action IS NOT NULL AND next_action_due_at IS NOT NULL))
);
CREATE TABLE qc_runs (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    task_id TEXT NOT NULL REFERENCES work_tasks(id),
    profile_version_id TEXT NOT NULL REFERENCES qc_profile_versions(id),
    overall TEXT NOT NULL CHECK (overall IN ('pass', 'fail', 'not_tested')),
    actor_staff_id TEXT NOT NULL REFERENCES accounts(id),
    recorded_at TEXT NOT NULL,
    reason_code TEXT
);
CREATE TABLE qc_results (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES qc_runs(id),
    check_id TEXT NOT NULL REFERENCES qc_profile_checks(id),
    result TEXT NOT NULL CHECK (result IN ('pass', 'fail', 'not_tested')),
    note TEXT,
    UNIQUE (run_id, check_id)
);
CREATE TRIGGER movements_no_update BEFORE UPDATE ON stock_movements BEGIN SELECT RAISE(ABORT, 'immutable_movement'); END;
CREATE TRIGGER movements_no_delete BEFORE DELETE ON stock_movements BEGIN SELECT RAISE(ABORT, 'immutable_movement'); END;
CREATE TRIGGER qc_runs_no_update BEFORE UPDATE ON qc_runs BEGIN SELECT RAISE(ABORT, 'immutable_qc'); END;
CREATE TRIGGER qc_runs_no_delete BEFORE DELETE ON qc_runs BEGIN SELECT RAISE(ABORT, 'immutable_qc'); END;
CREATE TRIGGER qc_results_no_update BEFORE UPDATE ON qc_results BEGIN SELECT RAISE(ABORT, 'immutable_qc'); END;
CREATE TRIGGER qc_results_no_delete BEFORE DELETE ON qc_results BEGIN SELECT RAISE(ABORT, 'immutable_qc'); END;
CREATE TRIGGER profile_versions_no_update BEFORE UPDATE ON qc_profile_versions BEGIN SELECT RAISE(ABORT, 'immutable_profile_version'); END;
CREATE TRIGGER profile_versions_no_delete BEFORE DELETE ON qc_profile_versions BEGIN SELECT RAISE(ABORT, 'immutable_profile_version'); END;
CREATE TRIGGER profile_checks_no_update BEFORE UPDATE ON qc_profile_checks BEGIN SELECT RAISE(ABORT, 'immutable_profile_check'); END;
CREATE TRIGGER profile_checks_no_delete BEFORE DELETE ON qc_profile_checks BEGIN SELECT RAISE(ABORT, 'immutable_profile_check'); END;
