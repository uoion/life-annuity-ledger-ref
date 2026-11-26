-- migrations/postgres/0001_initial_schema.sql
-- Production PostgreSQL schema with native NUMERIC precision, JSONB event payloads,
-- and PL/pgSQL immutability trigger enforcement.

CREATE TABLE IF NOT EXISTS policies (
    policy_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    policyholder_ref TEXT NOT NULL,
    age INTEGER NOT NULL CHECK (age >= 55 AND age <= 90),
    lump_sum_zar NUMERIC(14, 2) NOT NULL,
    guarantee_period_years INTEGER NOT NULL CHECK (guarantee_period_years IN (0, 5, 10, 15, 20)),
    escalation_rate_pct NUMERIC(5, 2) NOT NULL,
    annuity_type TEXT NOT NULL CHECK (annuity_type IN ('single_life', 'joint_life')),
    monthly_payout_zar NUMERIC(14, 2) NOT NULL,
    annual_payout_zar NUMERIC(14, 2) NOT NULL,
    quote_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'SUSPENDED', 'TERMINATED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    seq_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
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
    amount_zar NUMERIC(14, 2) NOT NULL,
    currency CHAR(3) NOT NULL DEFAULT 'ZAR' CHECK (currency = 'ZAR'),
    prev_hash CHAR(64) NOT NULL,
    entry_hash CHAR(64) NOT NULL,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ledger_policy_id ON ledger_entries(policy_id);

CREATE OR REPLACE FUNCTION prevent_ledger_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'ledger_entries is append-only: % forbidden', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ledger_immutable ON ledger_entries;
CREATE TRIGGER trg_ledger_immutable
BEFORE UPDATE OR DELETE ON ledger_entries
FOR EACH ROW EXECUTE FUNCTION prevent_ledger_mutation();

CREATE TABLE IF NOT EXISTS outbox_events (
    event_id TEXT PRIMARY KEY,
    aggregate_id TEXT NOT NULL REFERENCES policies(policy_id),
    event_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_json JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (
        status IN ('PENDING', 'PROCESSING', 'DISPATCHED', 'DEAD_LETTER')
    ),
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    max_retries INTEGER NOT NULL DEFAULT 3 CHECK (max_retries >= 1),
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    dispatched_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox_events(status, created_at);

CREATE TABLE IF NOT EXISTS idempotency_records (
    idempotency_key TEXT PRIMARY KEY,
    endpoint TEXT NOT NULL,
    request_hash CHAR(64) NOT NULL,
    response_status INTEGER NOT NULL,
    response_body JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
