-- migrations/sqlite/0001_initial_schema.sql
-- SQLite schema for zero-config local/CI execution.
-- Enforces append-only audit ledger immutability via database triggers.

CREATE TABLE IF NOT EXISTS policies (
    policy_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    policyholder_ref TEXT NOT NULL,
    age INTEGER NOT NULL CHECK (age >= 55 AND age <= 90),
    lump_sum_zar TEXT NOT NULL,
    guarantee_period_years INTEGER NOT NULL CHECK (guarantee_period_years IN (0, 5, 10, 15, 20)),
    escalation_rate_pct TEXT NOT NULL,
    annuity_type TEXT NOT NULL CHECK (annuity_type IN ('single_life', 'joint_life')),
    monthly_payout_zar TEXT NOT NULL,
    annual_payout_zar TEXT NOT NULL,
    quote_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'SUSPENDED', 'TERMINATED')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    seq_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL UNIQUE,
    policy_id TEXT NOT NULL REFERENCES policies(policy_id),
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'POLICY_ISSUED',
            'PAYOUT_SCHEDULED',
            'PAYOUT_DISBURSED',
            'PAYOUT_FAILED_DLQ',
            'ADJUSTMENT_REVERSAL'
        )
    ),
    amount_zar TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'ZAR' CHECK (currency = 'ZAR'),
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ledger_policy_id ON ledger_entries(policy_id);

CREATE TRIGGER IF NOT EXISTS trg_ledger_no_update
BEFORE UPDATE ON ledger_entries
BEGIN
    SELECT RAISE(ABORT, 'ledger_entries is append-only: UPDATE forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trg_ledger_no_delete
BEFORE DELETE ON ledger_entries
BEGIN
    SELECT RAISE(ABORT, 'ledger_entries is append-only: DELETE forbidden');
END;

CREATE TABLE IF NOT EXISTS outbox_events (
    event_id TEXT PRIMARY KEY,
    aggregate_id TEXT NOT NULL REFERENCES policies(policy_id),
    event_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (
        status IN ('PENDING', 'PROCESSING', 'DISPATCHED', 'DEAD_LETTER')
    ),
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    max_retries INTEGER NOT NULL DEFAULT 3 CHECK (max_retries >= 1),
    last_error TEXT,
    created_at TEXT NOT NULL,
    dispatched_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox_events(status, created_at);

CREATE TABLE IF NOT EXISTS idempotency_records (
    idempotency_key TEXT PRIMARY KEY,
    endpoint TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_status INTEGER NOT NULL,
    response_body TEXT NOT NULL,
    created_at TEXT NOT NULL
);
