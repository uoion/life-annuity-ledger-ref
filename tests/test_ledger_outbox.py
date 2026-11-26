from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.domain.models import PolicyIssueRequest
from app.infra.database import Database
from app.infra.ledger import verify_ledger_chain
from app.infra.outbox import (
    IdempotencyConflictError,
    dispatch_pending_outbox,
    issue_policy_transactional,
)


def _sample_request(ref: str = "PH-SYN-1001") -> PolicyIssueRequest:
    return PolicyIssueRequest(
        policyholder_ref=ref,
        age=66,
        lump_sum_zar=Decimal("2000000.00"),
        guarantee_period_years=10,
        escalation_rate_pct=Decimal("5.00"),
        annuity_type="single_life",
    )


def test_ledger_append_only_triggers_block_mutation_and_detect_tampering(db: Database) -> None:
    policy, replayed = issue_policy_transactional(
        db,
        request=_sample_request(),
        idempotency_key="idem-key-0001",
    )
    assert not replayed

    with pytest.raises(IntegrityError, match="UPDATE forbidden"):
        with db.engine.begin() as conn:
            conn.execute(
                text("UPDATE ledger_entries SET amount_zar = '0.00' WHERE policy_id = :pid"),
                {"pid": policy["policy_id"]},
            )

    with pytest.raises(IntegrityError, match="DELETE forbidden"):
        with db.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM ledger_entries WHERE policy_id = :pid"),
                {"pid": policy["policy_id"]},
            )

    with db.engine.connect() as conn:
        valid, checked, broken_id = verify_ledger_chain(conn)
    assert valid is True
    assert checked == 2
    assert broken_id is None

    # Simulate rogue DBA disabling trigger and altering a historical amount
    with db.engine.begin() as conn:
        conn.execute(text("DROP TRIGGER trg_ledger_no_update"))
        conn.execute(text("UPDATE ledger_entries SET amount_zar = '1.00' WHERE seq_id = 1"))

    with db.engine.connect() as conn:
        tampered_valid, _, tampered_id = verify_ledger_chain(conn)
    assert tampered_valid is False
    assert tampered_id is not None


def test_idempotency_replay_and_conflict(db: Database) -> None:
    req = _sample_request()
    first, replayed_1 = issue_policy_transactional(db, request=req, idempotency_key="idem-key-0002")
    second, replayed_2 = issue_policy_transactional(
        db, request=req, idempotency_key="idem-key-0002"
    )

    assert replayed_1 is False
    assert replayed_2 is True
    assert first["policy_id"] == second["policy_id"]

    mutated = _sample_request(ref="PH-SYN-9999")
    with pytest.raises(IdempotencyConflictError):
        issue_policy_transactional(db, request=mutated, idempotency_key="idem-key-0002")


def test_outbox_dead_letter_queue_after_max_retries(db: Database) -> None:
    issue_policy_transactional(db, request=_sample_request(), idempotency_key="idem-key-dlq-1")

    def failing_transport(_event: dict) -> None:
        raise ConnectionError("Downstream EFT gateway timeout")

    r1 = dispatch_pending_outbox(db, transport_fn=failing_transport)
    r2 = dispatch_pending_outbox(db, transport_fn=failing_transport)
    r3 = dispatch_pending_outbox(db, transport_fn=failing_transport)

    assert r1 == {"processed": 1, "dispatched": 0, "retried": 1, "dead_lettered": 0}
    assert r2 == {"processed": 1, "dispatched": 0, "retried": 1, "dead_lettered": 0}
    assert r3 == {"processed": 1, "dispatched": 0, "retried": 0, "dead_lettered": 1}

    with db.engine.connect() as conn:
        status_row = (
            conn.execute(text("SELECT status, retry_count, last_error FROM outbox_events"))
            .mappings()
            .one()
        )
        valid, checked, _ = verify_ledger_chain(conn)

    assert status_row["status"] == "DEAD_LETTER"
    assert status_row["retry_count"] == 3
    assert "EFT gateway timeout" in status_row["last_error"]
    assert valid is True
    assert checked == 3


def test_postgres_migration_parity_file_exists() -> None:
    pg_sql = Path("migrations/postgres/0001_initial_schema.sql").read_text(encoding="utf-8")
    assert "GENERATED ALWAYS AS IDENTITY" in pg_sql
    assert "JSONB" in pg_sql
    assert "BEFORE UPDATE OR DELETE ON ledger_entries" in pg_sql
