CREATE TABLE demo_accounts (
    account_key TEXT PRIMARY KEY CHECK (account_key IN (
        'staff.manager','staff.intake','staff.technician','staff.dual',
        'customer.a','customer.b','customer.invited'
    )),
    account_id TEXT NOT NULL UNIQUE REFERENCES accounts(id)
);
CREATE TABLE runtime_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
