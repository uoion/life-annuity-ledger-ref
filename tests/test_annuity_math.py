from decimal import Decimal

import pytest

from app.domain.annuity import calculate_annuity_quote


def test_deterministic_quote_is_repeatable_and_exact() -> None:
    q1 = calculate_annuity_quote(
        age=65,
        lump_sum_zar=Decimal("1000000.00"),
        guarantee_period_years=10,
        escalation_rate_pct=Decimal("5.00"),
        annuity_type="single_life",
    )
    q2 = calculate_annuity_quote(
        age=65,
        lump_sum_zar=Decimal("1000000.00"),
        guarantee_period_years=10,
        escalation_rate_pct=Decimal("5.00"),
        annuity_type="single_life",
    )

    assert q1 == q2
    assert q1.quote_hash == q2.quote_hash
    assert q1.monthly_payout_zar > Decimal("4500.00")
    assert q1.monthly_payout_zar < Decimal("6000.00")
    assert q1.annual_payout_zar == q1.monthly_payout_zar * Decimal("12")
    assert q1.guaranteed_total_zar == q1.annual_payout_zar * Decimal("10")


def test_actuarial_monotonicity_invariants() -> None:
    base = calculate_annuity_quote(
        age=65,
        lump_sum_zar=Decimal("1500000.00"),
        guarantee_period_years=10,
        escalation_rate_pct=Decimal("5.00"),
        annuity_type="single_life",
    )
    older = calculate_annuity_quote(
        age=75,
        lump_sum_zar=Decimal("1500000.00"),
        guarantee_period_years=10,
        escalation_rate_pct=Decimal("5.00"),
        annuity_type="single_life",
    )
    higher_escalation = calculate_annuity_quote(
        age=65,
        lump_sum_zar=Decimal("1500000.00"),
        guarantee_period_years=10,
        escalation_rate_pct=Decimal("8.00"),
        annuity_type="single_life",
    )
    joint_life = calculate_annuity_quote(
        age=65,
        lump_sum_zar=Decimal("1500000.00"),
        guarantee_period_years=10,
        escalation_rate_pct=Decimal("5.00"),
        annuity_type="joint_life",
    )
    longer_guarantee = calculate_annuity_quote(
        age=65,
        lump_sum_zar=Decimal("1500000.00"),
        guarantee_period_years=20,
        escalation_rate_pct=Decimal("5.00"),
        annuity_type="single_life",
    )

    assert older.monthly_payout_zar > base.monthly_payout_zar
    assert higher_escalation.monthly_payout_zar < base.monthly_payout_zar
    assert joint_life.monthly_payout_zar < base.monthly_payout_zar
    assert longer_guarantee.monthly_payout_zar < base.monthly_payout_zar


def test_zero_real_rate_edge_case() -> None:
    quote = calculate_annuity_quote(
        age=65,
        lump_sum_zar=Decimal("1000000.00"),
        guarantee_period_years=0,
        escalation_rate_pct=Decimal("9.50"),
    )
    assert quote.monthly_payout_zar > Decimal("0.00")


@pytest.mark.parametrize(
    ("kwargs", "expected_msg"),
    [
        ({"age": 50}, "age must be between"),
        ({"lump_sum_zar": Decimal("1000.00")}, "lump_sum_zar must be between"),
        ({"guarantee_period_years": 7}, "guarantee_period_years must be one of"),
        ({"escalation_rate_pct": Decimal("12.00")}, "escalation_rate_pct must be between"),
        ({"annuity_type": "invalid"}, "annuity_type must be"),
    ],
)
def test_invalid_inputs_rejected(kwargs: dict, expected_msg: str) -> None:
    params = {
        "age": 65,
        "lump_sum_zar": Decimal("1000000.00"),
        "guarantee_period_years": 10,
        "escalation_rate_pct": Decimal("5.00"),
    }
    params.update(kwargs)
    with pytest.raises(ValueError, match=expected_msg):
        calculate_annuity_quote(**params)
