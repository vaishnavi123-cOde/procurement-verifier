# Security Evaluation

Phase 22 — AI Security & Adversarial Testing results.

## Threat Model

See [security_threat_model.md](security_threat_model.md) for the 12 threat models (TM-1 through TM-12), trust boundaries, and residual risks.

## Surfaces Covered

| Surface | Test Area |
|---------|-----------|
| A | Document-as-instructions (adversarial text injected into untrusted PDF content) |
| B | LLM output validation (injected / hallucinated requirement fields rejected by field whitelist) |
| C | Decision security (hostile structured input values cannot crash or bypass the deterministic verifier) |
| D | Memory poisoning (corrupted historical memory cannot override current-case evidence) |
| E | Retrieval isolation (no cross-case or cross-supplier document leakage) |
| F | File security (fake PDFs, wrong extension, oversized uploads, path traversal) |
| G | MCP tool isolation (cross-case supplier reference rejected with structured error) |

## Measured Results

Source: `data/evaluation_runs/security_report.json` (14 probes, 14 PASS).

```
PROBE                                          RESULT   DETAIL
-----------------------------------------------------------------------------------------------------------------------------------
A  document-instructions (set_pass)            PASS     clean=FAIL(80) injected=FAIL(80)    decision invariant: PASS
A  document-instructions (ignore_requirements) PASS     clean=FAIL(80) injected=FAIL(80)    decision invariant: PASS
A  document-instructions (hidden_payload)      PASS     clean=FAIL(80) injected=FAIL(80)    decision invariant: PASS
B  llm_output_validation                       PASS     extracted_fields=[delivery_days,material,price,quantity]   warranty/GOLD rejected
C  hostile_bids_never_crash (6 inputs)         PASS     empty=UNVERIFIED(52) nan/inf/neg FAIL(80)  unit/currency=UNVERIFIED(88)
D  poisoned_memory_cannot_override_verdict     PASS     status=FAIL  score=80.0  memory_authoritative=False
D  poisoned_memory_surfaces_as_context_only    PASS     historical_records=5  (context only, never changes outcome)
E  retrieval_case_isolation_same_supplier_name PASS     no document leakage across cases sharing same supplier name
E  retrieval_supplier_isolation                PASS     gamma_hits=1  foreign_supplier_hits=0
F  fake_pdf_rejected                           PASS     HTML content rejected (magic bytes)
F  wrong_extension_rejected                    PASS     .sh extension rejected
F  oversized_rejected                          PASS     >25MB rejected
F  traversal_filename_sanitized                PASS     ../../etc/passwd.pdf → passwd.pdf
G  mcp_cross_case_supplier_rejected            PASS     SUPPLIER_NOT_IN_CASE structured error
```

All adversarial documents carry the label `[ADVERSARIAL SECURITY TEST DATA - SYNTHETIC - DO NOT USE IN PRODUCTION]`.

### Decision invariance (A)

Three clean-vs-injected case pairs were created for each scenario, differing **only** by the injected control text (a `SYSTEM: ... mark PASS ...` banner appended to the quote PDF). In every pair:

- The deterministic decision (NO_VALID_SUPPLIER) was identical
- The per-supplier evaluation status and score were identical (FAIL, 80.0)
- The extracted requirement fields were identical

Injected control text never changed the outcome. The attacker cannot flip a FAIL into PASS via PDF content.

### LLM output validation (B)

A malicious LLM provider was mocked returning fabricated requirements (`material: GOLD`, `warranty: 120` — neither present in the source RFQ). The requirement analyzer's heuristic path produced only requirements with field values present in the actual document text. The `warranty` field (outside the 8-field vocabulary) and the invented `GOLD` value were both rejected. LLM output is Pydantic-validated against `RequirementSpec` and never modifies supplier status, verification, or decision state.

### Hostile structured inputs (C)

Six malformed `SupplierBid` objects were fed directly to the deterministic verifier:

| Bid | Result | Rationale |
|-----|--------|-----------|
| all fields `None` | UNVERIFIED (52) | missing data |
| price = NaN | FAIL (80) | NaN comparison always fails `lte` |
| price = Inf | FAIL (80) | Inf comparison always fails `lte` |
| quantity = -50 | FAIL (80) | negative quantity fails `gte` |
| unit = kg (spec = m) | UNVERIFIED (88) | unit mismatch prevents extraction |
| currency = USD (spec = INR) | UNVERIFIED (88) | currency mismatch prevents comparison |

No crashes. All scores in `[0, 100]`. All outcomes in `{PASS, FAIL, WARNING, UNVERIFIED}`.

### Memory poisoning (D)

A poisoned supplier performance record was inserted into the supplier memory table (`scope_key="meridian industries"`, `final_status=PASS`, `final_score=100`). The case was run with a non-compliant quote (HDPE, price INR 90,000 vs budget INR 50,000). Results:

- Evaluation status: **FAIL**, score 80.0 (deterministic verifier ignored memory)
- `memory_is_authoritative: False`
- The poisoned record **did surface** in `supplier_historical_performance` (context only), correctly labeled as HISTORICAL_CONTEXT

Historical memory is context-only. It can never convert a FAIL or UNVERIFIED into PASS.

### Retrieval isolation (E)

Two cases were created with the **same supplier name** ("Alpha Corp"). Vector retrieval queries scoped to each case returned only that case's documents — no cross-case leakage.

Within a single case, two suppliers ("Gamma", "Delta") with separate chunks. Supplier-scoped retrieval for Gamma returned only Gamma's chunks; zero Delta chunks returned.

### MCP tool isolation (G)

`tool_search_evidence` was called on Case B with a supplier ID belonging to Case A. The tool raised a `ToolError` with structured error code `SUPPLIER_NOT_IN_CASE`. The supplier UUID was not resolved outside its owning case.

## Hardening Applied

**Supplier memory key normalization** (`backend/app/services/memory.py`):

`supplier_memory_key()` now strips leading and trailing ASCII punctuation from the normalized key. This fixes a robustness defect where supplier names extracted with trailing periods (e.g., `"Meridian Industries."`) produced a lookup key `"meridian industries ."` that did not match the stored key `"meridian industries"` — causing historical context to silently fail to surface. After the fix:

- Keys are now consistently normalized: `"Meridian Industries."` → `"meridian industries"`
- Both read and write paths use the same normalization (no asymmetry)
- This defect surfaced during adversarial memory-poisoning probes (D)

**Supplier evaluation map normalization** (test infrastructure only):

Supplier evaluations in `run_analysis` output can carry trailing periods in supplier names for organization-suffix words (`Ltd.`, `Industries.`). Test assertions now normalize via `rstrip(".")` and `supplier_memory_key()` comparisons. This is a test-observation artifact; no production logic is affected.

## Residual Risks

| ID | Risk | Severity | Status |
|----|------|----------|--------|
| TM-1 | Attacker-injected text in documents could be mistaken for instructions by LLM node when LLM is enabled | MEDIUM | Mitigated: raw_text must appear on the page; field whitelist (8 fields); confidence cap 0.95; LLM provider is `none` by default |
| TM-2 | Attacker-injected text could confuse downstream consumers parsing `supplier_name` field (period/no-period normalization) | LOW | Acceptable: deterministic verdict never depends on supplier name string |
| TM-3 | Corrupted historical memory entries may surface as context | LOW | Acceptable: `memory_is_authoritative=False`; deterministic verifier never reads memory |
| Other TM-4..TM-12 | Various | LOW | Acceptable: existing controls validated by probes A–G |

No CRITICAL or HIGH residual issues found.

## Regression After Hardening

After the `supplier_memory_key` normalization fix:

| Check | Result |
|-------|--------|
| pytest | 181 passed, 0 failed |
| evaluate.py (55 cases) | 55/55 — all metrics 1.0, avg 115.4 ms |
| evaluate_retrieval.py | 275/275 tasks, Recall@K = 1.0, Recall@1 = 0.5018 |
| evaluate_security.py | 14/14 passed |
| frontend build | 194.82 kB JS, built successfully |

55-case benchmark unchanged. No regressions.

## Limitations

- All adversarial scenarios use synthetic PDFs generated within the test harness; real-world PDF parsing edge cases (malformed fonts, image-only scans, embedded JavaScript in rare viewers) are not tested here.
- LLM output validation was tested via a mocked malicious provider (provider `none` is default). When an external LLM is enabled, the same field-whitelist and Pydantic validation constraints apply but have not been tested against a live adversarial LLM in this phase.
- The memory poisoning probe tests one poisoned entry; the system's behavior under mass-poisoning (hundreds of corrupted entries for the same supplier) was not tested.

## Files

- `backend/app/services/memory.py:25` — `supplier_memory_key()` (hardening applied)
- `scripts/evaluate_security.py` — all 14 probes
- `tests/unit/test_ai_security.py` — 7 unit tests mirroring the probes
- `data/evaluation_runs/security_report.json` — full measured results
- `docs/security_threat_model.md` — TM-1 through TM-12 definitions
