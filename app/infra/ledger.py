import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import Connection, text

GENESIS_HASH = "0" * 64


def compute_entry_hash(
    *,
    prev_hash: str,
    entry_id: str,
    policy_id: str,
    event_type: str,
    amount_zar: str,
    payload_json: str,
    created_at: str,
) -> str:
    """Compute SHA-256 tamper-evident hash linking this entry to the prior chain tip."""
    canonical = json.dumps(
        {
            "prev_hash": prev_hash,
            "entry_id": entry_id,
            "policy_id": policy_id,
            "event_type": event_type,
            "amount_zar": amount_zar,
            "payload_json": payload_json,
            "created_at": created_at,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def append_ledger_entry(
    conn: Connection,
    *,
    policy_id: str,
    event_type: str,
    amount_zar: Decimal,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Append an immutable hash-chained audit entry within an active SQL transaction."""
    row = conn.execute(
        text("SELECT entry_hash FROM ledger_entries ORDER BY seq_id DESC LIMIT 1")
    ).fetchone()
    prev_hash = row[0] if row is not None else GENESIS_HASH

    entry_id = f"LED-{uuid4().hex[:12].upper()}"
    created_at = datetime.now(UTC).isoformat()
    amount_str = str(amount_zar.quantize(Decimal("0.01")))
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    entry_hash = compute_entry_hash(
        prev_hash=prev_hash,
        entry_id=entry_id,
        policy_id=policy_id,
        event_type=event_type,
        amount_zar=amount_str,
        payload_json=payload_json,
        created_at=created_at,
    )

    conn.execute(
        text(
            """
            INSERT INTO ledger_entries (
                entry_id, policy_id, event_type, amount_zar, currency,
                prev_hash, entry_hash, payload_json, created_at
            ) VALUES (
                :entry_id, :policy_id, :event_type, :amount_zar, 'ZAR',
                :prev_hash, :entry_hash, :payload_json, :created_at
            )
            """
        ),
        {
            "entry_id": entry_id,
            "policy_id": policy_id,
            "event_type": event_type,
            "amount_zar": amount_str,
            "prev_hash": prev_hash,
            "entry_hash": entry_hash,
            "payload_json": payload_json,
            "created_at": created_at,
        },
    )

    return {
        "entry_id": entry_id,
        "policy_id": policy_id,
        "event_type": event_type,
        "amount_zar": amount_str,
        "currency": "ZAR",
        "prev_hash": prev_hash,
        "entry_hash": entry_hash,
        "created_at": created_at,
    }


def list_ledger_entries(conn: Connection, policy_id: str | None = None) -> list[dict[str, Any]]:
    if policy_id:
        rows = conn.execute(
            text(
                """
                SELECT seq_id, entry_id, policy_id, event_type, amount_zar, currency,
                       prev_hash, entry_hash, created_at
                FROM ledger_entries
                WHERE policy_id = :policy_id
                ORDER BY seq_id ASC
                """
            ),
            {"policy_id": policy_id},
        ).mappings()
    else:
        rows = conn.execute(
            text(
                """
                SELECT seq_id, entry_id, policy_id, event_type, amount_zar, currency,
                       prev_hash, entry_hash, created_at
                FROM ledger_entries
                ORDER BY seq_id ASC
                """
            )
        ).mappings()

    return [dict(row) for row in rows]


def verify_ledger_chain(conn: Connection) -> tuple[bool, int, str | None]:
    """Walk the entire audit ledger from genesis and verify every SHA-256 link."""
    rows = conn.execute(
        text(
            """
            SELECT entry_id, policy_id, event_type, amount_zar,
                   prev_hash, entry_hash, payload_json, created_at
            FROM ledger_entries
            ORDER BY seq_id ASC
            """
        )
    ).mappings()

    expected_prev = GENESIS_HASH
    checked = 0
    for row in rows:
        checked += 1
        if row["prev_hash"] != expected_prev:
            return False, checked, row["entry_id"]

        payload_raw = row["payload_json"]
        if not isinstance(payload_raw, str):
            payload_raw = json.dumps(payload_raw, sort_keys=True, separators=(",", ":"))

        recomputed = compute_entry_hash(
            prev_hash=row["prev_hash"],
            entry_id=row["entry_id"],
            policy_id=row["policy_id"],
            event_type=row["event_type"],
            amount_zar=str(row["amount_zar"]),
            payload_json=payload_raw,
            created_at=str(row["created_at"]),
        )
        if row["entry_hash"] != recomputed:
            return False, checked, row["entry_id"]

        expected_prev = row["entry_hash"]

    return True, checked, None
