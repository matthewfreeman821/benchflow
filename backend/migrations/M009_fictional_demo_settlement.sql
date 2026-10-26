CREATE TABLE simulated_payments (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    amount_minor INTEGER NOT NULL CHECK(typeof(amount_minor)='integer' AND amount_minor BETWEEN 1 AND 10000000000),
    currency TEXT NOT NULL CHECK(currency='USD'),
    recorded_at TEXT NOT NULL,
    actor_staff_id TEXT NOT NULL REFERENCES accounts(id),
    reason_code TEXT NOT NULL
);
CREATE INDEX simulated_payments_job ON simulated_payments(job_id,id);

CREATE TABLE simulated_handovers (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
    kind TEXT NOT NULL CHECK(kind='completed_repair'),
    collector_name TEXT NOT NULL,
    acknowledgement TEXT NOT NULL CHECK(acknowledgement='Fictional demo only; no property was physically transferred.'),
    recorded_at TEXT NOT NULL,
    actor_staff_id TEXT NOT NULL REFERENCES accounts(id),
    balance_minor_at_handover INTEGER NOT NULL CHECK(typeof(balance_minor_at_handover)='integer' AND balance_minor_at_handover=0)
);
