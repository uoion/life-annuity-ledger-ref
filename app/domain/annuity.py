"""Deterministic synthetic ZAR Guaranteed Life Annuity calculator.

IMPORTANT:
- Financial calculations execute strictly in deterministic Python using
  decimal.Decimal (ROUND_HALF_UP) — never via floating-point math or LLMs.
- Models a synthetic Guaranteed Life Annuity (longevity risk pooled by insurer,
  paid monthly in arrears, gross before SARS/PAYE tax withholding).
- Uses an illustrative present-value approximation for reference architecture
  demonstration and is not an FSCA/SAM-certified actuarial pricing basis.
"""

import hashlib
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

CALCULATOR_VERSION = "v1.0.0-synthetic"
ANNUAL_DISCOUNT_RATE = Decimal("0.095")
EXPENSE_LOADING_RATE = Decimal("0.0175")
VALID_GUARANTEE_YEARS = (0, 5, 10, 15, 20)
MIN_AGE = 55
MAX_AGE = 90
MIN_LUMP_SUM_ZAR = Decimal("50000.00")
MAX_LUMP_SUM_ZAR = Decimal("50000000.00")
MIN_ESCALATION_PCT = Decimal("0.00")
MAX_ESCALATION_PCT = Decimal("10.00")

AnnuityType = Literal["single_life", "joint_life"]


@dataclass(frozen=True, slots=True)
class QuoteCalculation:
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


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_annuity_quote(
    *,
    age: int,
    lump_sum_zar: Decimal,
    guarantee_period_years: int,
    escalation_rate_pct: Decimal,
    annuity_type: AnnuityType = "single_life",
) -> QuoteCalculation:
    """Compute a deterministic gross ZAR guaranteed life annuity quote."""
    if not (MIN_AGE <= age <= MAX_AGE):
        raise ValueError(f"age must be between {MIN_AGE} and {MAX_AGE}")
    if not (MIN_LUMP_SUM_ZAR <= lump_sum_zar <= MAX_LUMP_SUM_ZAR):
        raise ValueError(f"lump_sum_zar must be between {MIN_LUMP_SUM_ZAR} and {MAX_LUMP_SUM_ZAR}")
    if guarantee_period_years not in VALID_GUARANTEE_YEARS:
        raise ValueError(f"guarantee_period_years must be one of {VALID_GUARANTEE_YEARS}")
    if not (MIN_ESCALATION_PCT <= escalation_rate_pct <= MAX_ESCALATION_PCT):
        raise ValueError(
            f"escalation_rate_pct must be between {MIN_ESCALATION_PCT} and {MAX_ESCALATION_PCT}"
        )
    if annuity_type not in ("single_life", "joint_life"):
        raise ValueError("annuity_type must be 'single_life' or 'joint_life'")

    lump_sum = _quantize_money(lump_sum_zar)
    escalation = _quantize_money(escalation_rate_pct)

    # Synthetic longevity horizon with joint-life survivorship and guarantee certainty loading
    base_horizon = max(Decimal("8.0"), Decimal("92.0") - Decimal(age))
    if annuity_type == "joint_life":
        base_horizon += Decimal("4.5")

    guarantee_loading = Decimal(guarantee_period_years) * Decimal("0.12")
    effective_years = max(base_horizon, Decimal(guarantee_period_years)) + guarantee_loading

    g = escalation / Decimal("100")
    real_annual_rate = (Decimal("1") + ANNUAL_DISCOUNT_RATE) / (Decimal("1") + g) - Decimal("1")
    monthly_rate = real_annual_rate / Decimal("12")
    total_months = int((effective_years * Decimal("12")).to_integral_value(rounding=ROUND_HALF_UP))

    if abs(monthly_rate) < Decimal("1e-9"):
        pv_factor = Decimal(total_months)
    else:
        pv_factor = (Decimal("1") - (Decimal("1") + monthly_rate) ** (-total_months)) / monthly_rate

    net_principal = lump_sum * (Decimal("1") - EXPENSE_LOADING_RATE)
    monthly_payout = _quantize_money(net_principal / pv_factor)
    annual_payout = _quantize_money(monthly_payout * Decimal("12"))
    effective_rate_pct = _quantize_money((annual_payout / lump_sum) * Decimal("100"))
    guaranteed_total = _quantize_money(annual_payout * Decimal(guarantee_period_years))

    canonical = json.dumps(
        {
            "age": age,
            "lump_sum_zar": str(lump_sum),
            "guarantee_period_years": guarantee_period_years,
            "escalation_rate_pct": str(escalation),
            "annuity_type": annuity_type,
            "monthly_payout_zar": str(monthly_payout),
            "calculator_version": CALCULATOR_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    quote_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    return QuoteCalculation(
        age=age,
        lump_sum_zar=lump_sum,
        guarantee_period_years=guarantee_period_years,
        escalation_rate_pct=escalation,
        annuity_type=annuity_type,
        monthly_payout_zar=monthly_payout,
        annual_payout_zar=annual_payout,
        effective_annuity_rate_pct=effective_rate_pct,
        guaranteed_total_zar=guaranteed_total,
        effective_horizon_years=effective_years.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP),
        calculator_version=CALCULATOR_VERSION,
        quote_hash=quote_hash,
    )
