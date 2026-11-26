import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from app.domain.annuity import calculate_annuity_quote
from app.domain.models import PolicyIssueRequest
from app.infra.database import Database
from app.infra.ledger import append_ledger_entry


class IdempotencyConflictError(Exception):
    """Raised when an Idempotency-Key is reused with a different request payload."""


TransportFn = Callable[[dict[str, Any]], None]


def _default_transport(_event: dict[str, Any]) -> None:
    """In-process deterministic disbursement ack for offline/CI execution."""
    return None


def _hash_payload(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def issue_policy_transactional(
    db: Database,
    *,
    request: PolicyIssueRequest,
    idempotency_key: str,
) -> tuple[dict[str, Any], bool]:
    """Atomically issue a policy, append audit ledger entries, and enqueue outbox payout event.

    Returns (policy_dict, is_replayed).
    """
    request_payload = request.model_dump(mode="json")
    request_hash = _hash_payload(request_payload)

    with db.engine.begin() as conn:
        existing = (
            conn.execute(
                text(
                    """
                    SELECT request_hash, response_body
                    FROM idempotency_records
                    WHERE idempotency_key = :key
                    """
                ),
                {"key": idempotency_key},
            )
            .mappings()
            .fetchone()
        )

        if existing is not None:
            if existing["request_hash"] != request_hash:
                raise IdempotencyConflictError(
                    "Idempotency-Key was already used with a different request payload."
                )
            body = existing["response_body"]
            return (json.loads(body) if isinstance(body, str) else body), True

        quote = calculate_annuity_quote(
            age=request.age,
            lump_sum_zar=request.lump_sum_zar,
            guarantee_period_years=request.guarantee_period_years,
            escalation_rate_pct=request.escalation_rate_pct,
            annuity_type=request.annuity_type,
        )

        policy_id = f"POL-{uuid4().hex[:10].upper()}"
        created_at = datetime.now(UTC).isoformat()

        conn.execute(
            text(
                """
                INSERT INTO policies (
                    policy_id, idempotency_key, policyholder_ref, age, lump_sum_zar,
                    guarantee_period_years, escalation_rate_pct, annuity_type,
                    monthly_payout_zar, annual_payout_zar, quote_hash, status, created_at
                ) VALUES (
                    :policy_id, :idempotency_key, :policyholder_ref, :age, :lump_sum_zar,
                    :guarantee_period_years, :escalation_rate_pct, :annuity_type,
                    :monthly_payout_zar, :annual_payout_zar, :quote_hash, 'ACTIVE', :created_at
                )
                """
            ),
            {
                "policy_id": policy_id,
                "idempotency_key": idempotency_key,
                "policyholder_ref": request.policyholder_ref,
                "age": quote.age,
                "lump_sum_zar": str(quote.lump_sum_zar),
                "guarantee_period_years": quote.guarantee_period_years,
                "escalation_rate_pct": str(quote.escalation_rate_pct),
                "annuity_type": quote.annuity_type,
                "monthly_payout_zar": str(quote.monthly_payout_zar),
                "annual_payout_zar": str(quote.annual_payout_zar),
                "quote_hash": quote.quote_hash,
                "created_at": created_at,
            },
        )

        append_ledger_entry(
            conn,
            policy_id=policy_id,
            event_type="POLICY_ISSUED",
            amount_zar=quote.lump_sum_zar,
            payload={
                "policyholder_ref": request.policyholder_ref,
                "quote_hash": quote.quote_hash,
                "calculator_version": quote.calculator_version,
            },
        )

        append_ledger_entry(
            conn,
            policy_id=policy_id,
            event_type="PAYOUT_SCHEDULED",
            amount_zar=quote.monthly_payout_zar,
            payload={
                "installment_number": 1,
                "annuity_type": quote.annuity_type,
                "guarantee_period_years": quote.guarantee_period_years,
            },
        )

        outbox_event_id = f"EVT-{uuid4().hex[:12].upper()}"
        outbox_payload = {
            "policy_id": policy_id,
            "policyholder_ref": request.policyholder_ref,
            "amount_zar": str(quote.monthly_payout_zar),
            "currency": "ZAR",
            "installment_number": 1,
        }
        conn.execute(
            text(
                """
                INSERT INTO outbox_events (
                    event_id, aggregate_id, event_type, idempotency_key,
                    payload_json, status, retry_count, max_retries, created_at
                ) VALUES (
                    :event_id, :aggregate_id, 'PAYOUT_DISBURSEMENT_REQUESTED', :idempotency_key,
                    :payload_json, 'PENDING', 0, 3, :created_at
                )
                """
            ),
            {
                "event_id": outbox_event_id,
                "aggregate_id": policy_id,
                "idempotency_key": f"payout:{policy_id}:001",
                "payload_json": json.dumps(outbox_payload, sort_keys=True, separators=(",", ":")),
                "created_at": created_at,
            },
        )

        policy_response = {
            "policy_id": policy_id,
            "idempotency_key": idempotency_key,
            "policyholder_ref": request.policyholder_ref,
            "age": quote.age,
            "lump_sum_zar": str(quote.lump_sum_zar),
            "guarantee_period_years": quote.guarantee_period_years,
            "escalation_rate_pct": str(quote.escalation_rate_pct),
            "annuity_type": quote.annuity_type,
            "monthly_payout_zar": str(quote.monthly_payout_zar),
            "annual_payout_zar": str(quote.annual_payout_zar),
            "quote_hash": quote.quote_hash,
            "status": "ACTIVE",
            "created_at": created_at,
        }

        conn.execute(
            text(
                """
                INSERT INTO idempotency_records (
                    idempotency_key, endpoint, request_hash,
                    response_status, response_body, created_at
                ) VALUES (
                    :idempotency_key, 'POST /api/v1/policies', :request_hash,
                    201, :response_body, :created_at
                )
                """
            ),
            {
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
                "response_body": json.dumps(policy_response, sort_keys=True),
                "created_at": created_at,
            },
        )

        return policy_response, False


def dispatch_pending_outbox(
    db: Database,
    *,
    transport_fn: TransportFn | None = None,
) -> dict[str, int]:
    """Atomically claim PENDING outbox events and dispatch with retry ceiling + DLQ routing."""
    transport = transport_fn or _default_transport
    processed = 0
    dispatched = 0
    retried = 0
    dead_lettered = 0

    with db.engine.connect() as conn:
        pending_rows = (
            conn.execute(
                text(
                    """
                    SELECT event_id, aggregate_id, event_type, idempotency_key,
                           payload_json, retry_count, max_retries
                    FROM outbox_events
                    WHERE status = 'PENDING'
                    ORDER BY created_at ASC
                    """
                )
            )
            .mappings()
            .fetchall()
        )

    for row in pending_rows:
        event_dict = dict(row)

        # Atomic state-transition claim prevents concurrent duplicate dispatch
        with db.engine.begin() as conn:
            claim = conn.execute(
                text(
                    """
                    UPDATE outbox_events
                    SET status = 'PROCESSING'
                    WHERE event_id = :event_id AND status = 'PENDING'
                    """
                ),
                {"event_id": event_dict["event_id"]},
            )
            if claim.rowcount != 1:
                continue

        processed += 1
        raw_payload = event_dict["payload_json"]
        payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        amount_zar = Decimal(payload["amount_zar"])
        now_iso = datetime.now(UTC).isoformat()

        try:
            transport(event_dict)
        except Exception as exc:
            next_retry = event_dict["retry_count"] + 1
            is_dlq = next_retry >= event_dict["max_retries"]
            next_status = "DEAD_LETTER" if is_dlq else "PENDING"

            with db.engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE outbox_events
                        SET status = :status,
                            retry_count = :retry_count,
                            last_error = :last_error
                        WHERE event_id = :event_id AND status = 'PROCESSING'
                        """
                    ),
                    {
                        "status": next_status,
                        "retry_count": next_retry,
                        "last_error": str(exc)[:500],
                        "event_id": event_dict["event_id"],
                    },
                )
                if is_dlq:
                    append_ledger_entry(
                        conn,
                        policy_id=event_dict["aggregate_id"],
                        event_type="PAYOUT_FAILED_DLQ",
                        amount_zar=amount_zar,
                        payload={
                            "outbox_event_id": event_dict["event_id"],
                            "retry_count": next_retry,
                            "error": str(exc)[:200],
                        },
                    )
                    dead_lettered += 1
                else:
                    retried += 1
            continue

        with db.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE outbox_events
                    SET status = 'DISPATCHED',
                        dispatched_at = :dispatched_at,
                        last_error = NULL
                    WHERE event_id = :event_id AND status = 'PROCESSING'
                    """
                ),
                {"dispatched_at": now_iso, "event_id": event_dict["event_id"]},
            )
            append_ledger_entry(
                conn,
                policy_id=event_dict["aggregate_id"],
                event_type="PAYOUT_DISBURSED",
                amount_zar=amount_zar,
                payload={
                    "outbox_event_id": event_dict["event_id"],
                    "idempotency_key": event_dict["idempotency_key"],
                },
            )
            dispatched += 1

    return {
        "processed": processed,
        "dispatched": dispatched,
        "retried": retried,
        "dead_lettered": dead_lettered,
    }
