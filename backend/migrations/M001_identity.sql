CREATE TABLE accounts (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('staff', 'customer')),
    state TEXT NOT NULL CHECK (state IN ('invited', 'active', 'suspended', 'closed')),
    display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 2000),
    login_id TEXT,
    login_normalized TEXT UNIQUE,
    password_hash TEXT,
    credential_state TEXT NOT NULL CHECK (credential_state IN ('usable', 'reset_required', 'not_set')),
    account_rev INTEGER NOT NULL DEFAULT 1 CHECK (account_rev >= 1),
    auth_rev INTEGER NOT NULL DEFAULT 1 CHECK (auth_rev >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((password_hash IS NOT NULL) = (credential_state = 'usable')),
    CHECK ((login_id IS NULL) = (login_normalized IS NULL)),
    CHECK (state != 'active' OR login_normalized IS NOT NULL)
);
CREATE TABLE staff_roles (
    account_id TEXT NOT NULL REFERENCES accounts(id),
    role TEXT NOT NULL CHECK (role IN ('intake', 'technician', 'manager')),
    PRIMARY KEY (account_id, role)
);
CREATE TRIGGER staff_roles_staff_only BEFORE INSERT ON staff_roles
WHEN (SELECT kind FROM accounts WHERE id = NEW.account_id) != 'staff'
BEGIN SELECT RAISE(ABORT, 'staff_role_kind'); END;
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY CHECK (length(token_hash) = 64),
    csrf_hash TEXT NOT NULL CHECK (length(csrf_hash) = 64),
    account_id TEXT REFERENCES accounts(id),
    auth_rev INTEGER,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    idle_expires_at TEXT NOT NULL,
    absolute_expires_at TEXT NOT NULL,
    revocation_reason TEXT CHECK (revocation_reason IN (
        'roles_changed', 'suspended', 'closed', 'recovered', 'ownership_changed', 'logout'
    )),
    CHECK ((account_id IS NULL) = (auth_rev IS NULL))
);
CREATE INDEX sessions_account ON sessions(account_id);
CREATE TABLE invitations (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    state TEXT NOT NULL CHECK (state IN ('pending', 'revoked', 'expired', 'superseded', 'redeemed')),
    owner_staff_id TEXT NOT NULL REFERENCES accounts(id),
    next_action_due_at TEXT NOT NULL,
    verification_method TEXT CHECK (verification_method IN ('in_person', 'phone')),
    verified_by_staff_id TEXT NOT NULL REFERENCES accounts(id),
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    contact_handoff_note TEXT,
    CHECK (expires_at > issued_at),
    CHECK (next_action_due_at > issued_at AND next_action_due_at < expires_at)
);
CREATE UNIQUE INDEX invitations_one_pending ON invitations(account_id) WHERE state = 'pending';
CREATE TABLE auth_codes (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE CHECK (length(token_hash) = 64),
    account_id TEXT NOT NULL REFERENCES accounts(id),
    invitation_id TEXT REFERENCES invitations(id),
    purpose TEXT NOT NULL CHECK (purpose IN ('invite', 'recovery')),
    state TEXT NOT NULL CHECK (state IN ('pending', 'revoked', 'expired', 'superseded', 'redeemed')),
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    issued_by_staff_id TEXT NOT NULL REFERENCES accounts(id),
    verification_method TEXT CHECK (verification_method IN ('in_person', 'phone', 'manager_verified')),
    redeemed_at TEXT,
    CHECK (expires_at > issued_at),
    CHECK ((purpose = 'invite') = (invitation_id IS NOT NULL))
);
CREATE UNIQUE INDEX codes_one_pending ON auth_codes(account_id, purpose) WHERE state = 'pending';
CREATE INDEX codes_account ON auth_codes(account_id);
CREATE TABLE auth_throttles (
    subject_hash TEXT NOT NULL,
    peer_hash TEXT NOT NULL,
    purpose TEXT NOT NULL CHECK (purpose IN ('login', 'activate', 'recover')),
    failures INTEGER NOT NULL CHECK (failures >= 0),
    window_started_at TEXT NOT NULL,
    blocked_until TEXT,
    PRIMARY KEY (subject_hash, peer_hash, purpose)
);
