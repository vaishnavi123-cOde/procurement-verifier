# Security Threat Model

Scope: the Evidence-Backed Procurement Decision & Verification System — a
LangGraph pipeline (case_loader → document_discovery → requirement_analyzer →
extraction → evidence → memory → deterministic verification → evidence
retrieval → critic → decision → memory write) plus REST API, RAG (Qdrant +
BM25), memory store, file ingestion, and a stdio MCP server.

## Trust boundaries

| Boundary | Contents | Trust level |
| --- | --- | --- |
| **A. System / application** | The codebase, structured app data, verification engine | Trusted (root of trust) |
| **B. Operator data** | Procurement cases, requirements, memory entries, evals | Trusted only through verified pipeline |
| **C. Untrusted documents** | Supplier quotes, certificates, history, RFQ text (PDF) | **UNTRUSTED — data, never instructions** |
| **D. LLM output** (only when `LLM_PROVIDER != none`) | Extracted requirements JSON from a 3rd-party model | **UNTRUSTED until validated** |

- Documents (C) can contain anything — including text that *looks* like
  commands ("IGNORE ALL REQUIREMENTS", "set status PASS"). It must be treated
  strictly as **data**.
- LLM output (D) is untrusted AI output. It must pass structural + provenance
  validation before any influence on requirements.
- The deterministic engine (A) is the only authority that assigns
  PASS / FAIL / WARNING / UNVERIFIED and picks the recommendation. Neither C nor
  D may change its logic.

## Threat model

### TM-1 Prompt injection (via documents)
- **Vector:** attacker-controlled PDF text embedded with imperative
  instructions is concatenated into an LLM user prompt (`graphs/nodes.py:149`,
  max 3000 chars/page, LLM assist only).
- **Impact if exploited:** injected requirement could change which suppliers
  are checked — not the engine logic.
- **Controls:** LLM off by default (`LLM_PROVIDER=none`); LLM output must
  contain `raw_text` physically present on the page and only
  material/quantity/price/delivery_days/certification fields (`nodes.py:156-172`);
  deterministic verifier never consumes LLM output; failure is non-fatal.
- **Residual:** LOW.

### TM-2 Injected instructions in document text (control-plane confusion)
- **Vector:** supplier PDF contains "SYSTEM:", "set PASS", "confidence 0.99",
  "mark compliant", "ignore requirements" text.
- **Impact:** an operator or a downstream tool could mistake injected prose for
  an evaluation verdict, or the extractor could skin instruction text.
- **Controls:** verdicts are computed only from structured extracted values;
  instruction/command vocabulary is not part of the extraction grammar; the
  UI/audit view labels the evidence chain (Requirement → Evidence →
  Verification → Critic → Decision) and shows provenance.
- **Residual:** MEDIUM (human-confusion vector) — mitigated by audit.

### TM-3 LLM output poisoning / hallucinated requirements
- **Vector:** LLM returns requirements not present in documents, or skewed
  values.
- **Controls:** `raw_text` substring check against normalized page text,
  field whitelist, confidence cap 0.95 (`nodes.py:156-177`).
- **Residual:** LOW.

### TM-4 Cross-case retrieval leakage
- **Vector:** attacker reads case A's documents/evidence by querying case B.
- **Controls:** Qdrant `case_id` filter (`rag/vector_store.py`), BM25 SQL
  `case_id` where (`rag/hybrid.py:98-133`), evidence retrieval node always
  passes `case_id`, storage isolation (`services/ingestion.py:46-48`).
- **Residual:** LOW.

### TM-5 Supplier-confusion / same-name collision across cases
- **Vector:** supplier "Acme Ltd" in case A vs case B; attacker wants A's
  evidence returned under B.
- **Controls:** supplier IDs are case-local; retrieval filters by case_id and
  optional supplier_id; MCP tools validate `supplier.case_id == case.id` before
  supplier-scoped search (`mcp/tools.py:118-128`).
- **Residual:** LOW.

### TM-6 Memory poisoning
- **Vector:** attacker writes/induces a memory entry (e.g. via a crafted prior
  case) claiming a non-compliant supplier "passed with score 100".
- **Controls:** memory is context, never authority (`services/memory.py:3-5`):
  write only happens for completed, critic-unblocked runs from structured facts
  (`memory_writer.py`); retrieval only populates `historical_context`; the
  verifier never reads it (`graphs/nodes.py:291-302,337-356`).
- **Residual:** LOW — poisoned memory can only mislead a human reading history.

### TM-7 Verifier decision bypass
- **Vector:** hostile bid values (None/empty/NaN/huge), unit/currency confusion,
  tampered structured input.
- **Controls:** engine is pure functions on typed Pydantic inputs; missing
  values → UNVERIFIED; uncomparable units/currencies → UNVERIFIED; critic can
  only downgrade to ABSTAIN, never upgrade (`services/critic.py`),
  `nodes.py:525-542`.
- **Residual:** LOW.

### TM-8 File upload abuse
- **Vector:** non-PDF payloads, oversized files, path traversal filenames,
  duplicate-file exhaustion.
- **Controls:** extension whitelist, 25 MB cap, `%PDF` magic-byte check,
  SHA256 dedup per case, `safe_filename()`, resolved-path must stay inside
  upload root.
- **Residual:** LOW.

### TM-9 API abuse / unauthorized access
- **Vector:** cross-case document/execution/evidence access, SQL injection,
  path traversal via params, token forgery, rate-limit bypass.
- **Controls:** strict path-param regex (`^[A-Za-z0-9_-]{1,128}$`), SQLAlchemy
  ORM only, optional HMAC auth (`AUTH_ENABLED`) with bootstrap token, in-memory
  rate limiter (opt-in), safe global exception handler (`main.py:49-70`).
- **Residual:** MEDIUM by default because `AUTH_ENABLED=false` — documented as
  a mandatory production setting.

### TM-10 MCP surface abuse
- **Vector:** an MCP client calls tools with cross-case ids, or tries to
  trigger analysis with arbitrary case ids, or expects shell/exec capability.
- **Controls:** stdio transport (not network-exposed in compose); 9/10 tools
  read-only; `run_case_analysis` runs the standard pipeline only; no subprocess/
  eval/exec anywhere; case/supplier scoping enforced in tools.
- **Residual:** LOW (must stay local-only).

### TM-11 Data exfiltration via evidence/document APIs
- **Vector:** fetch a document list, evidence list, or execution detail for a
  case the caller doesn't own, using IDs from another case.
- **Controls:** every retrieval is scoped by case_id at the repository layer;
  evidence/document access routes resolve within the owning case.
- **Residual:** LOW.

### TM-12 Secret disclosure
- **Vector:** secrets (LLM keys, JWT secret) leaked via logs or API responses.
- **Controls:** `Field(..., repr=False)` on secrets; structured-log redaction
  (`observability/logger.py`); `.env` gitignored; safe error handler never
  echoes internals unless `DEBUG/DETAIL_ERRORS` (off in production).
- **Residual:** LOW.

## Measured results

See `docs/security_evaluation.md` for the adversarial scenarios run by
`scripts/evaluate_security.py` (all results are real measured output; nothing
is fabricated).

## Hardening priority

1. CRITICAL — none found.
2. HIGH — none found (documented production defaults for AUTH remain a HIGH
   *risk if ignored*, not a code flaw; deployment guide mandates enabling it).
3. MEDIUM — human-confusion vector of injected prose (TM-2); mitigated by
   timing of decision attributes; document as operator guidance.
4. LOW — defense-in-depth items (e.g. per-case vector namespaces) documented as
   optional.