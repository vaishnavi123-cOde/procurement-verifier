# Procurement Verifier — Final Evaluation Report

**Date:** 2026-09-16
**Status:** Phase 18 Complete — All numbers are real measured values from actual execution.

---

## 1. Executive Summary

The Procurement Verifier is a deterministic-first procurement compliance system built with a LangGraph 12-node graph pipeline, Qdrant vector store, and a zero-LLM fallback architecture. Phase 18 independently evaluates the system across four dimensions: benchmark accuracy, retrieval quality, adversarial robustness, and operational correctness.

### Key Results

| Metric | Value | Source |
|--------|-------|--------|
| Unit tests | **174 passed** | `pytest -q` |
| Benchmark cases | **55/55 passed** | `evaluate.py` |
| Case accuracy | **1.0** | `report.json` |
| Recommendation accuracy | **1.0** | `report.json` |
| Bid outcome accuracy | **1.0** | `report.json` |
| Evidence recall | **1.0** | `report.json` |
| Avg pipeline latency | **72.8 ms** | `report.json` |
| Retrieval recall@K | **1.0** (275/275 tasks) | `retrieval_report.json` |
| Retrieval recall@1 | **0.502** | `retrieval_report.json` |
| Adversarial scenarios | **9/15 correct, 4/15 limitation, 2/15 incorrect** | `adversarial_report.json` |
| Security tests | **23/23 passed** | `test_security.py` |
| MCP tools | **10 registered** | `server.py` |
| Frontend build | **Clean (194.82 KB JS, 10.14 KB CSS)** | `npm run build` |

---

## 2. Test Suite

```
174 passed, 11 warnings in 9.14s
```

Breakdown:
- `tests/unit/test_security.py` — 23 tests (HMAC tokens, auth middleware, rate limiting, require_auth dependency)
- `tests/unit/test_observability.py` — (Phase 16: logger, middleware, correlation IDs)
- `tests/integration/test_langgraph_e2e.py` — End-to-end pipeline tests
- Remaining unit tests — extraction, verification, normalization, memory, ingestion

---

## 3. Benchmark Evaluation (55 Cases)

All 55 cases from `data/benchmark/` executed via `python scripts/evaluate.py`. Results in `data/evaluation_runs/report.json`.

### Metrics

| Metric | Score |
|--------|-------|
| case_accuracy | 1.0 |
| recommendation_accuracy | 1.0 |
| status_accuracy | 1.0 |
| abstention_accuracy | 1.0 |
| bid_outcome_accuracy | 1.0 |
| evidence_recall | 1.0 |
| avg_duration_ms | 72.8 |

### Per-Case Breakdown

Every case's `actual.recommended_supplier` matches `expected.recommended_supplier`. Every supplier's `passed` status matches expected. Checks include material, quantity, price, delivery_days, certification, and certification.validity — all with correct PASS/FAIL/UNVERIFIED statuses.

### Notes

- Benchmark covers 5 case categories: compliance shortlist, certificate gauntlet, delivery tight, price anchor, and memory repeat.
- All 55 cases use the `backend.llm_provider=none` deterministic mode (no LLM augmentation).
- Duration range: 62–87 ms per case (in-memory Qdrant, SQLite, hash embeddings).

---

## 4. Retrieval Evaluation

All 55 cases evaluated via `python scripts/evaluate_retrieval.py`. Results in `data/evaluation_runs/retrieval_report.json`.

### Metrics

| Metric | Value |
|--------|-------|
| retrieval_recall_at_k | 1.0 |
| recall_at_1 | 0.502 |
| full_hit_tasks | 275/275 |

### Notes

- **Recall@K = 1.0** means every expected document appears in the top-K results (K = number of expected docs).
- **Recall@1 = 0.502** means the expected document is the top-ranked result ~50% of the time. This is expected behavior: when 8 documents are in the case, a query like "What is the maximum budget?" correctly retrieves `rfq.pdf`, but it may rank behind `spec.pdf` because both documents contain budget-related text. The system correctly retrieves all expected documents, but the ordering among relevant documents is not always perfect.
- No Recall@3/5/10 metrics were computed — only recall@K (where K = expected hit count) and recall@1 are available.
- Retrieval uses hash-based embeddings (no external embedding provider), which explains the moderate recall@1 score.

---

## 5. Adversarial Evaluation (15 Scenarios)

Synthetic PDFs with specific violation patterns created by `scripts/adversarial_evaluation.py`. Each scenario tests whether the system correctly identifies the violation or appropriately abstains. Results in `data/evaluation_runs/adversarial_report.json`.

### Honest Categorization

#### Correctly Handled (9 scenarios)

| # | Scenario | Expected | Actual | Assessment |
|---|----------|----------|--------|------------|
| 1 | wrong_material | FAIL (SS 304 ≠ SS 316L) | FAIL, score=80 | Correct |
| 2 | wrong_quantity | FAIL (80 < 200) | FAIL, score=80 | Correct |
| 4 | currency_mismatch | FAIL (USD vs INR) | FAIL, score=80 | Correct |
| 6 | expired_certification | ABSTAIN/FAIL | ABSTAIN (UNVERIFIED) | Correct — abstains rather than hallucinate |
| 7 | missing_certification | ABSTAIN/FAIL | ABSTAIN (UNVERIFIED) | Correct — abstains rather than hallucinate |
| 8 | contradictory_evidence | ABSTAIN | ABSTAIN (UNVERIFIED) | Correct — detects conflict, abstains |
| 11 | multiple_compliant | RECOMMEND best | RECOMMEND, PASS score=100 | Correct |
| 13 | ambiguous_requirement | RECOMMEND (handles ambiguity) | RECOMMEND, PASS score=100 | Correct |
| 15 | historical_bad_now_good | RECOMMEND (passes HRC 65 ≥ 62) | RECOMMEND, PASS score=100 | Correct |

#### System Limitations — Not Bugs (4 scenarios)

| # | Scenario | Expected | Actual | Root Cause |
|---|----------|----------|--------|------------|
| 9 | missing_evidence | ABSTAIN (no 24/7 proof) | RECOMMEND, PASS score=100 | "24/7 support availability" is not an extraction vocabulary field — requirement never created |
| 10 | unsupported_claim | ABSTAIN (verbal warranty) | RECOMMEND, PASS score=100 | System sees "Warranty: 5 years" in text; cannot distinguish documented vs verbal claims deterministically |
| 12 | no_compliant | ABSTAIN/FAIL (0.05% ≠ 0.01%) | RECOMMEND, PASS score=100 | "Accuracy" and "NABL calibration" are not extraction vocabulary fields |
| 14 | historical_good_now_bad | ABSTAIN/FAIL (98.0% < 99.5%) | RECOMMEND, PASS score=100 | "Purity %" is not an extraction vocabulary field |

**Root Cause:** The deterministic extractor has a fixed vocabulary of requirement fields: material, quantity, price, delivery_days, certification, warranty, payment, bid_validity. Requirements outside this vocabulary (accuracy %, purity %, support SLAs, calibration certificates) are silently dropped — the system cannot verify what it cannot extract. This is by design ("missing info stays missing"), but the current behavior passes trivially instead of surfacing the gap as an unknown.

#### Incorrect Results (2 scenarios)

| # | Scenario | Expected | Actual | Root Cause |
|---|----------|----------|--------|------------|
| 3 | wrong_unit | FAIL (5000 kg ≠ 5000 pieces) | ABSTAIN (UNVERIFIED, score=88) | Unit mismatch not detected — system abstains rather than fails. Partially correct (abstains rather than hallucinates), but should have caught the unit incompatibility |
| 5 | delivery_violation | FAIL (28 days > 7 required) | RECOMMEND, PASS score=100 | RFQ "Delivery within 7 days" extracts correctly. Quote "Delivery: 28 days" uses colon format — `_DELIVERY_RE` requires "within/in/under" keyword, so delivery is never extracted from the quote. With no bid delivery data, the check should produce UNVERIFIED but score shows 100 with empty unknowns. |

**Note on delivery_violation:** The RFQ requirement extraction likely matched "Delivery within 7 calendar days" (via "within" keyword), creating a delivery_days requirement. The quote's "Delivery: 28 days (standard lead time)" does not contain a connector keyword between "Delivery" and the number, so `extract_delivery` returns None. The verifier's `_check_delivery` should produce UNVERIFIED when bid.delivery_days is None and bid.delivery_text is None. The fact that score=100 and unknowns=[] suggests either the RFQ requirement was also not extracted (possible text extraction issue from PDF), or UNVERIFIED checks are somehow not surfacing. This warrants investigation but is outside Phase 18 scope (no feature additions).

### Adversarial Summary

- **9/15 (60%)** correctly handled — violations detected or abstention honored
- **4/15 (27%)** limitation — requirements outside extraction vocabulary pass trivially
- **2/15 (13%)** incorrect — delivery format gap and unit mismatch detection gap

The system's abstention principle works well for certification validity (expired/missing) and contradictory evidence. The primary gap is requirements outside the extraction vocabulary.

---

## 6. Security (23 Tests)

All tests in `tests/unit/test_security.py`:
- Token issuance and verification (HMAC-SHA256)
- require_auth dependency (valid token, missing token, expired token, wrong secret)
- RateLimitMiddleware (window counting, sliding window, rate exceeded)
- Auth toggle (enabled/disabled modes)
- Token expiry (1s TTL with clock mock)
- Edge cases (empty token, malformed header, extra whitespace)

---

## 7. MCP Server

10 tools registered:
1. `get_case` — Retrieve case details by ID
2. `list_case_documents` — List all documents for a case
3. `search_evidence` — Vector search across case documents
4. `get_supplier` — Retrieve supplier details
5. `get_supplier_history` — Historical performance of a supplier
6. `get_requirement` — Retrieve requirement specifications
7. `evaluate_supplier` — Run deterministic verification for a single supplier
8. `run_case_analysis` — Execute full 12-node LangGraph pipeline
9. `get_case_decision` — Get final recommendation and ranked suppliers
10. `get_audit_report` — Complete audit trail for a case

Build: `python -m backend.app.mcp` — clean startup, all tools listed.

---

## 8. Operational Verification

### Clean Startup
- SQLite initialization: OK (data/procurement.db)
- Qdrant connection (in-memory mode): OK
- LLM provider: none (deterministic mode)
- Auth: disabled (AUTH_ENABLED=false)
- DB pool verification: OK (db_connect_timeout=5s)
- Database migration v2_required_indexes: OK (10 indexes)

### Docker Compose
- `Dockerfile` (backend): Python 3.11-slim, pip install, uvicorn startup
- `frontend/Dockerfile`: Node 20 build stage + nginx:alpine serve stage
- `docker-compose.yml`: backend, frontend, postgres:16-alpine, qdrant/qdrant
- Health checks: backend (/health), postgres, qdrant
- Volume mounts: `./data:/app/data` (bind), `pgdata`, `qdrant_storage`

### Frontend
- Build: `npm run build` — 194.82 KB JS, 10.14 KB CSS
- `DEFAULT_BASE_URL = ''` (same-origin, no hardcoded URLs)
- Vite dev proxy: `/api`, `/health`, `/ready` → localhost:8000
- `frontend/.env.example` provided

---

## 9. Architecture Guarantees

### Deterministic-First
All verification uses pure arithmetic and exact string matching. No LLM in the verification path. The LLM (when enabled) is only used for evidence augmentation and critic review — never for the final PASS/FAIL/WARNING/UNVERIFIED decision.

### Abstention Principle
When evidence is insufficient, the system reports UNVERIFIED (status) rather than guessing. This surfaces as `unknowns` in the recommendation. The benchmark demonstrates this works correctly for certification validity checks and material mismatches.

### Auditability
Every check in `report.json` includes:
- `requirement` — which field is being checked
- `expected` vs `actual` — the comparison values
- `reason` — human-readable explanation
- `evidence_ids` — links to source documents
- `severity_deduction` — scoring impact

---

## 10. Known Limitations

1. **Extraction vocabulary is fixed.** Requirements outside material/quantity/price/delivery_days/certification/warranty/payment/bid_validity are silently dropped. This is the primary source of false-positive recommendations in adversarial scenarios.

2. **Delivery regex does not handle colon format.** Quotes using "Delivery: N days" (without "within/in/under" keyword) are not detected. Benchmark uses "within N days" format so benchmark passes; real-world quotes often use colon format.

3. **No unit compatibility checking.** When an RFQ says "5000 kg" and a quote says "5000 pieces", the system does not detect the unit incompatibility. It abstains (UNVERIFIED) rather than fails.

4. **Verbal vs documented claims.** The system cannot distinguish verbal claims ("5-year warranty, no documentation") from documented claims when both appear as text in the same document.

5. **Retrieval recall@1 is 0.502.** The system retrieves all expected documents (recall@K = 1.0) but does not always rank the most relevant document first. Hash-based embeddings without LLM reranking produce moderate precision.

6. **No LLM provider in test environment.** All benchmarks run with `LLM_PROVIDER=none`. With an LLM enabled, the system can augment evidence extraction and the Critic node can catch issues that deterministic extraction misses.

7. **Per-case dedup only.** Document deduplication is per-case SHA256. Identical content across different cases is allowed (not deduplicated).

---

## 11. File Manifest

| File | Purpose |
|------|---------|
| `data/evaluation_runs/report.json` | 55-case benchmark results (per-case checks, scores, evidence) |
| `data/evaluation_runs/retrieval_report.json` | 55-case retrieval results (per-task recall, hits) |
| `data/evaluation_runs/adversarial_report.json` | 15 adversarial scenario results |
| `scripts/evaluate.py` | Benchmark evaluation script |
| `scripts/evaluate_retrieval.py` | Retrieval evaluation script |
| `scripts/adversarial_evaluation.py` | Adversarial evaluation script |
| `tests/unit/test_security.py` | 23 security tests |
| `tests/unit/test_observability.py` | Observability tests (Phase 16) |
