from app.domain.popia import contains_popia_pii, redact_popia_pii


def test_redacts_all_south_african_pii_categories_and_partial_name_tokens() -> None:
    raw = (
        "Client Mr Sipho Dlamini (ID 600101 5000 081) asked that Sipho receive confirmation via "
        "sipho.synthetic@example.co.za or +27 82 555 0199 regarding Account: 4058123490."
    )
    result = redact_popia_pii(raw, known_names=["Sipho Dlamini"])

    assert "600101" not in result.sanitized_text
    assert "sipho.synthetic@example.co.za" not in result.sanitized_text
    assert "+27 82 555 0199" not in result.sanitized_text
    assert "4058123490" not in result.sanitized_text
    assert "Sipho" not in result.sanitized_text
    assert "Dlamini" not in result.sanitized_text

    assert "[REDACTED_SA_ID]" in result.sanitized_text
    assert "[REDACTED_EMAIL]" in result.sanitized_text
    assert "[REDACTED_PHONE]" in result.sanitized_text
    assert "[REDACTED_ACCOUNT]" in result.sanitized_text
    assert "[REDACTED_NAME]" in result.sanitized_text
    assert result.total_redactions >= 6
    assert contains_popia_pii(result.sanitized_text, known_names=["Sipho Dlamini"]) is False


def test_preserves_non_pii_financial_context() -> None:
    raw = "Retiring at 65 and wants inflation protection for spouse over 10 years."
    result = redact_popia_pii(raw)
    assert result.sanitized_text == raw
    assert result.total_redactions == 0
    assert contains_popia_pii(raw) is False
