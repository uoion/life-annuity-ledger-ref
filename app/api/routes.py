from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy import text

from app.config import Settings
from app.domain.annuity import calculate_annuity_quote
from app.domain.models import (
    ExplainQuoteRequest,
    ExplainQuoteResponse,
    LedgerEntryResponse,
    LedgerVerifyResponse,
    OutboxDispatchResponse,
    OutboxEventResponse,
    PolicyIssueRequest,
    PolicyResponse,
    QuoteRequest,
    QuoteResponse,
)
from app.infra.ai_explainer import generate_governed_explanation
from app.infra.database import Database
from app.infra.ledger import list_ledger_entries, verify_ledger_chain
from app.infra.outbox import (
    IdempotencyConflictError,
    dispatch_pending_outbox,
    issue_policy_transactional,
)
from app.infra.rate_limiter import SlidingWindowRateLimiter

router = APIRouter(prefix="/api/v1", tags=["Life Annuities Core"])


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_rate_limiter(request: Request) -> SlidingWindowRateLimiter:
    return request.app.state.explain_rate_limiter


DbDep = Annotated[Database, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
RateLimiterDep = Annotated[SlidingWindowRateLimiter, Depends(get_rate_limiter)]


@router.post("/quotes", response_model=QuoteResponse)
def create_quote(payload: QuoteRequest) -> QuoteResponse:
    calc = calculate_annuity_quote(
        age=payload.age,
        lump_sum_zar=payload.lump_sum_zar,
        guarantee_period_years=payload.guarantee_period_years,
        escalation_rate_pct=payload.escalation_rate_pct,
        annuity_type=payload.annuity_type,
    )
    return QuoteResponse(**asdict(calc))


@router.post("/policies", response_model=PolicyResponse, status_code=status.HTTP_201_CREATED)
def issue_policy(
    payload: PolicyIssueRequest,
    response: Response,
    db: DbDep,
    idempotency_key: Annotated[
        str,
        Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
    ],
) -> PolicyResponse:
    try:
        policy, replayed = issue_policy_transactional(
            db,
            request=payload,
            idempotency_key=idempotency_key,
        )
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if replayed:
        response.headers["X-Idempotent-Replay"] = "true"
        response.status_code = status.HTTP_200_OK
    else:
        response.headers["X-Idempotent-Replay"] = "false"

    return PolicyResponse(**policy)


@router.get("/policies/{policy_id}", response_model=PolicyResponse)
def get_policy(policy_id: str, db: DbDep) -> PolicyResponse:
    with db.engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    """
                    SELECT policy_id, idempotency_key, policyholder_ref, age, lump_sum_zar,
                           guarantee_period_years, escalation_rate_pct, annuity_type,
                           monthly_payout_zar, annual_payout_zar, quote_hash, status, created_at
                    FROM policies
                    WHERE policy_id = :policy_id
                    """
                ),
                {"policy_id": policy_id},
            )
            .mappings()
            .fetchone()
        )

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found")

    return PolicyResponse(**dict(row))


@router.get("/ledger", response_model=list[LedgerEntryResponse])
def get_ledger(
    db: DbDep,
    policy_id: Annotated[str | None, Query()] = None,
) -> list[LedgerEntryResponse]:
    with db.engine.connect() as conn:
        entries = list_ledger_entries(conn, policy_id=policy_id)
    return [LedgerEntryResponse(**entry) for entry in entries]


@router.get("/ledger/verify", response_model=LedgerVerifyResponse)
def verify_ledger(db: DbDep) -> LedgerVerifyResponse:
    with db.engine.connect() as conn:
        valid, checked, broken_id = verify_ledger_chain(conn)
    return LedgerVerifyResponse(valid=valid, checked_entries=checked, broken_entry_id=broken_id)


@router.get("/outbox", response_model=list[OutboxEventResponse])
def get_outbox(
    db: DbDep,
    event_status: Annotated[str | None, Query(alias="status")] = None,
) -> list[OutboxEventResponse]:
    with db.engine.connect() as conn:
        if event_status:
            rows = conn.execute(
                text(
                    """
                    SELECT event_id, aggregate_id, event_type, idempotency_key, status,
                           retry_count, max_retries, last_error, created_at, dispatched_at
                    FROM outbox_events
                    WHERE status = :status
                    ORDER BY created_at ASC
                    """
                ),
                {"status": event_status},
            ).mappings()
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT event_id, aggregate_id, event_type, idempotency_key, status,
                           retry_count, max_retries, last_error, created_at, dispatched_at
                    FROM outbox_events
                    ORDER BY created_at ASC
                    """
                )
            ).mappings()
        return [OutboxEventResponse(**dict(r)) for r in rows]


@router.post("/outbox/dispatch", response_model=OutboxDispatchResponse)
def dispatch_outbox(db: DbDep) -> OutboxDispatchResponse:
    summary = dispatch_pending_outbox(db)
    return OutboxDispatchResponse(**summary)


@router.post("/explain-quote", response_model=ExplainQuoteResponse)
def explain_quote(
    payload: ExplainQuoteRequest,
    request: Request,
    settings: SettingsDep,
    rate_limiter: RateLimiterDep,
) -> ExplainQuoteResponse:
    client_key = request.client.host if request.client else "unknown"
    if not rate_limiter.allow(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded for /api/v1/explain-quote. Please retry shortly.",
        )

    calc = calculate_annuity_quote(
        age=payload.age,
        lump_sum_zar=payload.lump_sum_zar,
        guarantee_period_years=payload.guarantee_period_years,
        escalation_rate_pct=payload.escalation_rate_pct,
        annuity_type=payload.annuity_type,
    )
    result = generate_governed_explanation(
        quote=calc,
        audience=payload.audience,
        client_context=payload.client_context,
        client_name=payload.client_name,
        settings=settings,
    )
    return ExplainQuoteResponse(
        quote=QuoteResponse(**asdict(calc)),
        explanation=result.explanation,
        provider=result.provider,
        fallback_reason=result.fallback_reason,
        sanitized_context=result.sanitized_context,
        popia_redactions_applied=result.popia_redactions_applied,
    )
