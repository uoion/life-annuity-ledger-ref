from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.annuity import calculate_annuity_quote
from app.infra.ai_explainer import generate_governed_explanation
from app.main import create_app


def test_quote_endpoint_returns_deterministic_zar_calculation(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/quotes",
        json={
            "age": 65,
            "lump_sum_zar": "1500000.00",
            "guarantee_period_years": 10,
            "escalation_rate_pct": "5.00",
            "annuity_type": "single_life",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert Decimal(body["monthly_payout_zar"]) > Decimal("7000.00")
    assert len(body["quote_hash"]) == 64


def test_policy_issuance_idempotency_ledger_and_outbox_flow(client: TestClient) -> None:
    payload = {
        "policyholder_ref": "PH-SYN-2026A",
        "age": 68,
        "lump_sum_zar": "2500000.00",
        "guarantee_period_years": 15,
        "escalation_rate_pct": "4.50",
        "annuity_type": "joint_life",
    }
    headers = {"Idempotency-Key": "req-2026-11-26-0001"}

    created = client.post("/api/v1/policies", json=payload, headers=headers)
    assert created.status_code == 201
    assert created.headers["X-Idempotent-Replay"] == "false"
    policy_id = created.json()["policy_id"]

    replayed = client.post("/api/v1/policies", json=payload, headers=headers)
    assert replayed.status_code == 200
    assert replayed.headers["X-Idempotent-Replay"] == "true"
    assert replayed.json()["policy_id"] == policy_id

    conflict_payload = {**payload, "age": 70}
    conflict = client.post("/api/v1/policies", json=conflict_payload, headers=headers)
    assert conflict.status_code == 409

    fetched = client.get(f"/api/v1/policies/{policy_id}")
    assert fetched.status_code == 200
    assert fetched.json()["policyholder_ref"] == "PH-SYN-2026A"

    missing = client.get("/api/v1/policies/POL-DOESNOTEXIST")
    assert missing.status_code == 404

    dispatch = client.post("/api/v1/outbox/dispatch")
    assert dispatch.status_code == 200
    assert dispatch.json() == {"processed": 1, "dispatched": 1, "retried": 0, "dead_lettered": 0}

    outbox = client.get("/api/v1/outbox?status=DISPATCHED")
    assert outbox.status_code == 200
    assert len(outbox.json()) == 1

    ledger = client.get(f"/api/v1/ledger?policy_id={policy_id}")
    assert ledger.status_code == 200
    assert [e["event_type"] for e in ledger.json()] == [
        "POLICY_ISSUED",
        "PAYOUT_SCHEDULED",
        "PAYOUT_DISBURSED",
    ]

    verify = client.get("/api/v1/ledger/verify")
    assert verify.status_code == 200
    assert verify.json() == {"valid": True, "checked_entries": 3, "broken_entry_id": None}


def test_explain_quote_redacts_pii_and_uses_deterministic_template_offline(
    client: TestClient,
) -> None:
    resp = client.post(
        "/api/v1/explain-quote",
        json={
            "age": 65,
            "lump_sum_zar": "1200000.00",
            "guarantee_period_years": 10,
            "escalation_rate_pct": "5.00",
            "annuity_type": "single_life",
            "audience": "client_plain",
            "client_name": "Thabo Mokoena",
            "client_context": (
                "Thabo (ID 5806154800082, phone 082-555-1234, "
                "thabo.synthetic@example.co.za) wants predictable retirement income."
            ),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "deterministic_template"
    assert body["fallback_reason"] == "llm_unconfigured"
    assert "5806154800082" not in body["explanation"]
    assert "082-555-1234" not in body["explanation"]
    assert "thabo.synthetic@example.co.za" not in body["explanation"]
    assert "Thabo" not in body["explanation"]
    assert body["popia_redactions_applied"]["sa_id_number"] == 1
    assert body["popia_redactions_applied"]["email"] == 1
    assert body["popia_redactions_applied"]["sa_phone"] == 1
    assert body["popia_redactions_applied"]["known_name"] == 1


def test_explain_quote_enforces_rate_limit(tmp_path: Path) -> None:
    tight_settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'rl.db'}",
        llm_api_key="",
        explain_rate_limit_per_minute=2,
    )
    payload = {
        "age": 65,
        "lump_sum_zar": "1000000.00",
        "guarantee_period_years": 10,
        "escalation_rate_pct": "5.00",
    }
    with TestClient(create_app(settings=tight_settings)) as rl_client:
        assert rl_client.post("/api/v1/explain-quote", json=payload).status_code == 200
        assert rl_client.post("/api/v1/explain-quote", json=payload).status_code == 200
        third = rl_client.post("/api/v1/explain-quote", json=payload)
        assert third.status_code == 429


def test_llm_provider_fences_redacted_context_and_validates_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quote = calculate_annuity_quote(
        age=65,
        lump_sum_zar=Decimal("1000000.00"),
        guarantee_period_years=10,
        escalation_rate_pct=Decimal("5.00"),
    )
    captured_payload: dict[str, Any] = {}

    class _ValidResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                f"Illustrative summary: starting gross monthly income is "
                                f"R{quote.monthly_payout_zar:,.2f} before SARS/PAYE."
                            )
                        }
                    }
                ]
            }

    def _fake_valid_post(url: str, *, headers: dict, json: dict, timeout: float) -> _ValidResponse:
        captured_payload["url"] = url
        captured_payload["json"] = json
        return _ValidResponse()

    monkeypatch.setattr(httpx, "post", _fake_valid_post)
    settings = Settings(llm_api_key="sk-test-synthetic-never-real")

    ok_result = generate_governed_explanation(
        quote=quote,
        audience="advisor_summary",
        client_context="Client ID 6001015000081 email test@example.co.za wants steady income.",
        client_name=None,
        settings=settings,
    )

    assert ok_result.provider == "openai_compatible_llm"
    assert ok_result.fallback_reason is None
    assert captured_payload["json"]["max_tokens"] == 220
    sent_prompt = captured_payload["json"]["messages"][1]["content"]
    assert "<untrusted_client_context>" in sent_prompt
    assert "6001015000081" not in sent_prompt
    assert "test@example.co.za" not in sent_prompt
    assert "[REDACTED_SA_ID]" in sent_prompt
    assert "[REDACTED_EMAIL]" in sent_prompt

    # Fail-closed test: hallucinated/wrong Rand figure must be rejected -> deterministic_template
    class _HallucinatedResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {"content": "Your payout is R99,999.00 per month!"}}]}

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: _HallucinatedResponse())
    rejected = generate_governed_explanation(
        quote=quote,
        audience="client_plain",
        client_context="No PII here",
        client_name=None,
        settings=settings,
    )
    assert rejected.provider == "deterministic_template"
    assert rejected.fallback_reason == "validation_failed"
    assert f"R{quote.monthly_payout_zar:,.2f}" in rejected.explanation
