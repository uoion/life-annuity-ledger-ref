import json
import logging
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_health_ready_and_metrics_endpoints(client: TestClient) -> None:
    health_resp = client.get("/health")
    assert health_resp.status_code == 200
    assert health_resp.json()["status"] == "ok"
    assert "X-Correlation-ID" in health_resp.headers

    ready_resp = client.get("/ready")
    assert ready_resp.status_code == 200
    assert ready_resp.json() == {"status": "ready", "database": "sqlite"}

    metrics_resp = client.get("/metrics")
    assert metrics_resp.status_code == 200
    assert "text/plain" in metrics_resp.headers["content-type"]
    body = metrics_resp.text
    assert "annuity_process_uptime_seconds" in body
    assert "annuity_policies_total" in body
    assert "annuity_ledger_entries_total" in body
    assert 'annuity_outbox_events_total{status="DEAD_LETTER"}' in body
    assert 'annuity_http_requests_total{method="GET",route="/health",status="200"}' in body


def test_correlation_id_propagation_and_pii_free_json_access_log(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="annuity.access")

    resp = client.post(
        "/api/v1/explain-quote",
        headers={"X-Correlation-ID": "corr-sanlam-demo-001"},
        json={
            "age": 65,
            "lump_sum_zar": "1000000.00",
            "guarantee_period_years": 10,
            "escalation_rate_pct": "5.00",
            "client_name": "Naledi Khumalo",
            "client_context": "ID 6001015000081 email naledi@example.co.za",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["X-Correlation-ID"] == "corr-sanlam-demo-001"

    access_records = [r for r in caplog.records if r.name == "annuity.access"]
    assert len(access_records) >= 1
    parsed = json.loads(access_records[-1].getMessage())
    assert parsed["correlation_id"] == "corr-sanlam-demo-001"
    assert parsed["method"] == "POST"
    assert parsed["route"] == "/api/v1/explain-quote"
    assert parsed["status_code"] == 200
    assert isinstance(parsed["duration_ms"], float)
    raw_log = access_records[-1].getMessage()
    assert "6001015000081" not in raw_log
    assert "Naledi" not in raw_log
    assert "naledi@example.co.za" not in raw_log


def test_readiness_returns_503_when_database_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'broken.db'}",
        llm_api_key="",
    )
    app = create_app(settings=settings)
    with TestClient(app) as test_client:
        monkeypatch.setattr(
            app.state.db,
            "check_readiness",
            lambda: (_ for _ in ()).throw(RuntimeError("db down")),
        )
        resp = test_client.get("/ready")
        assert resp.status_code == 503
        assert resp.json()["status"] == "not_ready"


def test_kubernetes_manifests_and_dockerfile_security_invariants() -> None:
    dockerfile_text = Path("Dockerfile").read_text(encoding="utf-8")
    assert "USER 10001" in dockerfile_text
    assert "HEALTHCHECK" in dockerfile_text

    deployment = yaml.safe_load(Path("deploy/k8s/deployment.yaml").read_text(encoding="utf-8"))
    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    assert pod_spec["securityContext"]["runAsNonRoot"] is True
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["livenessProbe"]["httpGet"]["path"] == "/health"
    assert container["readinessProbe"]["httpGet"]["path"] == "/ready"
