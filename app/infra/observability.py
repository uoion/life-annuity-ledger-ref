"""Structured JSON access logging, correlation ID tracking, and Prometheus metrics.

Privacy Guardrail (POPIA):
Access logs record ONLY request metadata (correlation_id, method, route, status,
duration_ms) — never request/response bodies or client PII fields.
"""

import json
import logging
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from app.infra.database import Database

access_logger = logging.getLogger("annuity.access")


def configure_logging(level: str = "INFO") -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    access_logger.setLevel(numeric_level)
    if not access_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        access_logger.addHandler(handler)


def emit_access_log(
    *,
    correlation_id: str,
    method: str,
    route: str,
    status_code: int,
    duration_ms: float,
) -> None:
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "level": "INFO",
        "event": "http_request",
        "correlation_id": correlation_id,
        "method": method,
        "route": route,
        "status_code": status_code,
        "duration_ms": round(duration_ms, 2),
    }
    access_logger.info(json.dumps(record, separators=(",", ":")))


class MetricsRegistry:
    """Thread-safe Prometheus text-format metrics collector (zero external deps)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_at = time.monotonic()
        self._request_counts: dict[tuple[str, str, str], int] = defaultdict(int)
        self._request_duration_sum: dict[tuple[str, str], float] = defaultdict(float)
        self._request_duration_count: dict[tuple[str, str], int] = defaultdict(int)

    def record_request(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        status_str = str(status_code)
        with self._lock:
            self._request_counts[(method, route, status_str)] += 1
            self._request_duration_sum[(method, route)] += duration_seconds
            self._request_duration_count[(method, route)] += 1

    def _collect_db_gauges(self, db: Database) -> dict[str, Any]:
        gauges: dict[str, Any] = {
            "policies": 0,
            "ledger_entries": 0,
            "outbox_by_status": {
                "PENDING": 0,
                "PROCESSING": 0,
                "DISPATCHED": 0,
                "DEAD_LETTER": 0,
            },
        }
        try:
            with db.engine.connect() as conn:
                gauges["policies"] = int(
                    conn.execute(text("SELECT COUNT(*) FROM policies")).scalar_one()
                )
                gauges["ledger_entries"] = int(
                    conn.execute(text("SELECT COUNT(*) FROM ledger_entries")).scalar_one()
                )
                rows = conn.execute(
                    text("SELECT status, COUNT(*) FROM outbox_events GROUP BY status")
                ).fetchall()
                for status_name, count in rows:
                    gauges["outbox_by_status"][str(status_name)] = int(count)
        except Exception:
            return gauges
        return gauges

    def render_prometheus(self, db: Database) -> str:
        uptime_seconds = max(0.0, time.monotonic() - self._started_at)
        db_gauges = self._collect_db_gauges(db)

        lines: list[str] = [
            "# HELP annuity_process_uptime_seconds Seconds since API process start.",
            "# TYPE annuity_process_uptime_seconds gauge",
            f"annuity_process_uptime_seconds {uptime_seconds:.3f}",
            "# HELP annuity_policies_total Total active/recorded life annuity policies.",
            "# TYPE annuity_policies_total gauge",
            f"annuity_policies_total {db_gauges['policies']}",
            "# HELP annuity_ledger_entries_total Total immutable audit ledger entries.",
            "# TYPE annuity_ledger_entries_total gauge",
            f"annuity_ledger_entries_total {db_gauges['ledger_entries']}",
            "# HELP annuity_outbox_events_total Outbox disbursement events by status.",
            "# TYPE annuity_outbox_events_total gauge",
        ]

        for status_name, count in sorted(db_gauges["outbox_by_status"].items()):
            lines.append(f'annuity_outbox_events_total{{status="{status_name}"}} {count}')

        lines.extend(
            [
                "# HELP annuity_http_requests_total Total HTTP requests by method, route, status.",
                "# TYPE annuity_http_requests_total counter",
            ]
        )

        with self._lock:
            for (method, route, status_str), count in sorted(self._request_counts.items()):
                lines.append(
                    f'annuity_http_requests_total{{method="{method}",route="{route}",'
                    f'status="{status_str}"}} {count}'
                )

            lines.extend(
                [
                    "# HELP annuity_http_request_duration_seconds_sum Cumulative HTTP latency.",
                    "# TYPE annuity_http_request_duration_seconds_sum counter",
                ]
            )
            for (method, route), total_sec in sorted(self._request_duration_sum.items()):
                lines.append(
                    f'annuity_http_request_duration_seconds_sum{{method="{method}",'
                    f'route="{route}"}} {total_sec:.6f}'
                )

            lines.extend(
                [
                    "# HELP annuity_http_request_duration_seconds_count Observed HTTP requests.",
                    "# TYPE annuity_http_request_duration_seconds_count counter",
                ]
            )
            for (method, route), count in sorted(self._request_duration_count.items()):
                lines.append(
                    f'annuity_http_request_duration_seconds_count{{method="{method}",'
                    f'route="{route}"}} {count}'
                )

        return "\n".join(lines) + "\n"
