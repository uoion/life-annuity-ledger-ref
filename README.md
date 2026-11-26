# Life Annuity Ledger Reference Architecture

[![CI — Quality, Security, Container & IaC](https://github.com/uoion/life-annuity-ledger-ref/actions/workflows/ci.yml/badge.svg)](https://github.com/uoion/life-annuity-ledger-ref/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-OpenAPI%203.1-009688)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

A cloud-native, containerized **ZAR Guaranteed Life Annuities** backend reference architecture demonstrating full-lifecycle engineering ownership for regulated South African financial services:

* **Deterministic Financial Core:** Exact `decimal.Decimal` (`ROUND_HALF_UP`) annuity pricing with SHA-256 `quote_hash` audit reproducibility — financial arithmetic is **never** delegated to an LLM.
* **Tamper-Evident Audit Ledger:** Append-only SQL ledger protected by both database-level immutability triggers (`UPDATE`/`DELETE` aborted) and a verifiable SHA-256 hash chain (`GET /api/v1/ledger/verify`).
* **Duplicate-Proof Event-Driven Disbursements:** Atomic policy issuance combining strict `Idempotency-Key` replay/conflict (`409`) handling, a transactional **Outbox Pattern** with `PENDING -> PROCESSING` row claiming, and automatic Dead-Letter Queue (`DEAD_LETTER`) routing.
* **POPIA & ISO/IEC 42001 Governed AI:** Outbound PII redaction (SA IDs, names/tokens, emails, phone numbers, bank accounts), prompt-injection fencing, provider-agnostic OpenAI-compatible HTTP config, and a **fail-closed output validator** that reverts to a deterministic template if an LLM is unconfigured, unreachable, alters a Rand figure, or echoes PII.
* **Production Hardening:** Multi-stage non-root Docker (`UID 10001`), read-only root filesystem, `/health`, `/ready`, `/metrics` (Prometheus text format), PII-free JSON access logs with `X-Correlation-ID`, GitHub Actions CI (`ruff`, `bandit`, `pytest` >=85%, live container smoke test), AWS `af-south-1` (Cape Town) Terraform, and hardened Kubernetes manifests.

> **Domain Disclaimer:** Pricing formulas model a synthetic **Guaranteed Life Annuity** (gross monthly payout in arrears before SARS/PAYE tax withholding) for architectural demonstration only. This repository is a reference architecture, not an FSCA/SAM-certified actuarial engine.

---

## Architecture Overview

```mermaid
flowchart LR
    Client["Client / Future SPA<br/>(React · Angular · Vue)<br/>OpenAPI 3.1 + CORS"] -->|X-Correlation-ID<br/>Idempotency-Key| API["FastAPI Service<br/>Non-Root Container (UID 10001)"]

    subgraph Core["Deterministic Core (Zero LLM Math)"]
        API --> Calc["Decimal ZAR Pricer<br/>ROUND_HALF_UP + quote_hash"]
        Calc --> Tx["Single ACID SQL Transaction<br/>(SQLite local/CI · PostgreSQL prod)"]
        Tx --> Pol["policies"]
        Tx --> Led["ledger_entries<br/>(Append-Only Triggers + SHA-256 Chain)"]
        Tx --> Out["outbox_events<br/>(PENDING -> PROCESSING -> DISPATCHED/DLQ)"]
        Tx --> Idem["idempotency_records<br/>(Replay 200 · Conflict 409)"]
    end

    Out -->|Worker Claim + Retry Ceiling| Bus["Disbursement Transport<br/>(Local Ack · AWS SQS + DLQ)"]

    subgraph AI["Governed AI Summarization (Optional)"]
        API --> RL["Rate Limiter (429)"]
        RL --> Redact["POPIA PII Redactor<br/>+ Prompt Injection Fence"]
        Redact -->|LLM_API_KEY set| LLM["Provider-Agnostic OpenAI-Compat LLM<br/>(Regional Bedrock/Azure SA/Ollama)"]
        LLM --> Val{"Fail-Closed Validator<br/>Exact ZAR + Zero PII?"}
        Val -->|Pass| OutResp["Verified AI Summary"]
        Val -->|Fail or Unconfigured| Tpl["Deterministic Template<br/>(100% Offline Fallback)"]
        Redact -->|No Key| Tpl
    end

    API --> Ops["/health · /ready · /metrics<br/>PII-Free JSON Logs"]
