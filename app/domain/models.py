from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.annuity import AnnuityType


class QuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    age: int = Field(..., ge=55, le=90, examples=[65])
    lump_sum_zar: Decimal = Field(
        ..., ge=Decimal("50000.00"), le=Decimal("50000000.00"), examples=["1500000.00"]
    )
    guarantee_period_years: Literal[0, 5, 10, 15, 20] = Field(default=10, examples=[10])
    escalation_rate_pct: Decimal = Field(
        default=Decimal("5.00"), ge=Decimal("0.00"), le=Decimal("10.00"), examples=["5.00"]
    )
    annuity_type: AnnuityType = Field(default="single_life", examples=["single_life"])


class QuoteResponse(BaseModel):
    age: int
    lump_sum_zar: Decimal
    guarantee_period_years: int
    escalation_rate_pct: Decimal
    annuity_type: AnnuityType
    monthly_payout_zar: Decimal
    annual_payout_zar: Decimal
    effective_annuity_rate_pct: Decimal
    guaranteed_total_zar: Decimal
    effective_horizon_years: Decimal
    calculator_version: str
    quote_hash: str
    disclaimer: str = (
        "Synthetic Guaranteed Life Annuity reference calculation (gross monthly in arrears, "
        "before SARS/PAYE withholding); not an FSCA-licensed actuarial quote."
    )


class PolicyIssueRequest(QuoteRequest):
    policyholder_ref: str = Field(
        ...,
        min_length=4,
        max_length=64,
        pattern=r"^[A-Z0-9_-]+$",
        description="Pseudonymized internal policyholder reference (never raw SA ID).",
        examples=["PH-SYN-00421"],
    )


class PolicyResponse(BaseModel):
    policy_id: str
    idempotency_key: str
    policyholder_ref: str
    age: int
    lump_sum_zar: Decimal
    guarantee_period_years: int
    escalation_rate_pct: Decimal
    annuity_type: AnnuityType
    monthly_payout_zar: Decimal
    annual_payout_zar: Decimal
    quote_hash: str
    status: str
    created_at: str


class LedgerEntryResponse(BaseModel):
    seq_id: int
    entry_id: str
    policy_id: str
    event_type: str
    amount_zar: Decimal
    currency: str
    prev_hash: str
    entry_hash: str
    created_at: str


class LedgerVerifyResponse(BaseModel):
    valid: bool
    checked_entries: int
    broken_entry_id: str | None = None


class OutboxEventResponse(BaseModel):
    event_id: str
    aggregate_id: str
    event_type: str
    idempotency_key: str
    status: str
    retry_count: int
    max_retries: int
    last_error: str | None = None
    created_at: str
    dispatched_at: str | None = None


class OutboxDispatchResponse(BaseModel):
    processed: int
    dispatched: int
    retried: int
    dead_lettered: int


class ExplainQuoteRequest(QuoteRequest):
    audience: Literal["client_plain", "advisor_summary"] = Field(default="client_plain")
    client_name: str | None = Field(
        default=None,
        max_length=120,
        description="Optional client name; stripped by POPIA redactor before any AI call.",
    )
    client_context: str = Field(
        default="",
        max_length=1000,
        description="Optional free-text context; automatically scrubbed by POPIA middleware.",
    )


class ExplainQuoteResponse(BaseModel):
    quote: QuoteResponse
    explanation: str
    provider: Literal["openai_compatible_llm", "deterministic_template"]
    fallback_reason: str | None = None
    sanitized_context: str
    popia_redactions_applied: dict[str, int]
