-- Unknown historical provenance stays NULL. Never infer a catalog link from text.
ALTER TABLE estimate_lines ADD COLUMN part_id TEXT REFERENCES parts(id);
ALTER TABLE diagnostic_permissions ADD COLUMN fee_policy_rev INTEGER CHECK(fee_policy_rev >= 1);
ALTER TABLE estimate_drafts ADD COLUMN tax_source TEXT CHECK(tax_source IN ('not_configured','manual_shop_entry'));
ALTER TABLE estimates ADD COLUMN tax_source TEXT CHECK(tax_source IN ('not_configured','manual_shop_entry'));
ALTER TABLE estimates ADD COLUMN tax_policy_rev INTEGER CHECK(tax_policy_rev >= 1);
-- Rebuild only to widen the old charge-source CHECK for a tax snapshot.
-- Preserve every old column byte-for-byte; unknown source remains NULL.
CREATE TABLE settlement_entries_new (
    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
    bucket TEXT NOT NULL CHECK(bucket IN ('charge','credit','payment','refund')),
    amount_minor INTEGER NOT NULL CHECK(typeof(amount_minor)='integer' AND amount_minor BETWEEN 0 AND 10000000000),
    currency TEXT NOT NULL CHECK(currency='USD'),
    estimate_id TEXT REFERENCES estimates(id), line_id TEXT REFERENCES estimate_lines(id),
    permission_id TEXT REFERENCES diagnostic_permissions(id),
    payment_id TEXT REFERENCES settlement_entries(id), charge_id TEXT REFERENCES settlement_entries(id),
    credit_kind TEXT CHECK(credit_kind IN ('correction','write_off','customer_credit')),
    method TEXT CHECK(method IN ('cash','card_external','bank_external','other_external')),
    external_reference TEXT, occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL, authorized_at TEXT,
    actor_staff_id TEXT NOT NULL REFERENCES accounts(id), reason_code TEXT,
    source TEXT CHECK(source IN ('estimate_line','estimate_tax','diagnostic_fee')), tax_label TEXT,
    CHECK(bucket!='charge' OR ((estimate_id IS NOT NULL AND line_id IS NOT NULL) OR permission_id IS NOT NULL
        OR (source='estimate_tax' AND estimate_id IS NOT NULL AND tax_label IS NOT NULL))),
    CHECK(bucket!='refund' OR payment_id IS NOT NULL),
    CHECK(bucket!='credit' OR credit_kind IS NOT NULL),
    CHECK(bucket!='payment' OR method IS NOT NULL)
);
INSERT INTO settlement_entries_new SELECT *,NULL,NULL FROM settlement_entries;
DROP TRIGGER entries_no_update;
DROP TRIGGER entries_no_delete;
DROP TABLE settlement_entries;
ALTER TABLE settlement_entries_new RENAME TO settlement_entries;
CREATE INDEX settlement_job ON settlement_entries(job_id,bucket,id);
CREATE INDEX settlement_payment ON settlement_entries(payment_id);
CREATE TRIGGER entries_no_update BEFORE UPDATE ON settlement_entries BEGIN SELECT RAISE(ABORT,'immutable_money'); END;
CREATE TRIGGER entries_no_delete BEFORE DELETE ON settlement_entries BEGIN SELECT RAISE(ABORT,'immutable_money'); END;
ALTER TABLE work_tasks ADD COLUMN task_rev INTEGER NOT NULL DEFAULT 1 CHECK(task_rev >= 1);
CREATE TABLE audit_events_new (
    id TEXT PRIMARY KEY,
    aggregate_type TEXT NOT NULL CHECK(aggregate_type IN ('job','account','customer','part','qc_profile','return_case','contact_change_request','shop','backup')),
    aggregate_id TEXT NOT NULL, event_type TEXT NOT NULL,
    event_version INTEGER NOT NULL DEFAULT 1 CHECK(event_version >= 1), command_id TEXT,
    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('staff','customer','operator','system')), actor_id TEXT,
    operator_provenance_json TEXT CHECK(operator_provenance_json IS NULL OR json_valid(operator_provenance_json)),
    verification_method TEXT CHECK(verification_method IN ('in_person','phone')),
    occurred_at TEXT NOT NULL, reason_code TEXT, summary_code TEXT NOT NULL,
    refs_json TEXT NOT NULL CHECK(json_valid(refs_json))
);
INSERT INTO audit_events_new SELECT * FROM audit_events;
DROP TRIGGER audit_no_update;
DROP TRIGGER audit_no_delete;
DROP TABLE audit_events;
ALTER TABLE audit_events_new RENAME TO audit_events;
CREATE INDEX events_aggregate ON audit_events(aggregate_id,occurred_at,id);
CREATE INDEX events_command ON audit_events(command_id);
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT,'immutable_audit'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_events BEGIN SELECT RAISE(ABORT,'immutable_audit'); END;
CREATE TABLE job_dispositions (
    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
    disposition_rev INTEGER NOT NULL DEFAULT 1 CHECK(disposition_rev >= 1),
    kind TEXT NOT NULL CHECK(kind IN ('no_fault_found','unrepairable','dispute')),
    state TEXT NOT NULL CHECK(state IN ('recorded','open','resolved')),
    summary TEXT NOT NULL, actor_staff_id TEXT NOT NULL REFERENCES accounts(id),
    recorded_at TEXT NOT NULL, owner_staff_id TEXT NOT NULL REFERENCES accounts(id),
    next_action TEXT NOT NULL, next_action_due_at TEXT NOT NULL,
    resolved_at TEXT, resolved_by_staff_id TEXT REFERENCES accounts(id), resolution_summary TEXT,
    CHECK ((state='resolved') = (resolved_at IS NOT NULL AND resolved_by_staff_id IS NOT NULL AND resolution_summary IS NOT NULL)),
    CHECK ((kind='dispute' AND state IN ('open','resolved')) OR (kind!='dispute' AND state='recorded'))
);
CREATE TABLE backup_operations (
    command_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL REFERENCES accounts(id),
    created_at TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','complete')),
    manifest_json TEXT CHECK(manifest_json IS NULL OR json_valid(manifest_json)),
    event_id TEXT REFERENCES audit_events(id)
);
