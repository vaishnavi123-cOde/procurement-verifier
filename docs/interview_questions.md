# Interview Questions & Answers

Technical questions covering the architecture, design decisions, and implementation of the Evidence-Backed Procurement Decision & Verification System.

---

## 1. Why use LangGraph?

LangGraph provides a structured way to define a multi-step pipeline with explicit state passing between nodes. Each node is independently testable and the graph topology is declared, not implicit. The 12-node sequential pipeline (case_loader → document_discovery → ... → memory_write) benefits from:

- **Explicit state** — Structured data flows through the graph, not free-form messages
- **Node isolation** — Each node can be tested, timed, and failed independently
- **Deterministic execution** — Sequential graph with no ambiguity about execution order
- **Observability** — Each node run is logged with duration and status

An alternative would be a simple function chain, but LangGraph adds node-level error handling, state serialization, and a clean separation of concerns that scales to 12 nodes without becoming tangled.

---

## 2. Why hybrid RAG?

Procurement documents have two retrieval needs:

1. **Semantic matching** — "Who can supply stainless steel pipes?" needs understanding that "SS 316L" and "stainless steel" are related
2. **Exact keyword matching** — "ISO 9001:2015" must match exactly, not approximately

Vector search (Qdrant) handles semantic matching well but can return loosely related results. BM25 handles exact keyword matching well but misses semantic connections. By scoring and merging both result sets, the system gets the strengths of each approach.

---

## 3. Why BM25 + vector retrieval?

BM25 is a proven keyword-based retrieval algorithm that excels at exact term matching — critical for procurement where "SS 316L" and "SS 304" must not be confused. Vector embeddings capture semantic similarity. The merge strategy:

- Vector results scored by cosine similarity
- BM25 results scored by TF-IDF weighting
- Scores normalized and combined
- Top-K documents returned

This produces recall@K = 1.0 on the benchmark (all expected documents retrieved in every case).

---

## 4. Why deterministic verification?

Procurement compliance involves exact, verifiable facts:
- Is the material SS 316L? (exact string match after normalization)
- Is the price below budget? (arithmetic comparison)
- Is the quantity sufficient? (numeric comparison with operator)
- Is the certification valid? (date comparison against current date)
- Is the delivery within the deadline? (numeric comparison)

These are not opinions or interpretations — they are facts. An LLM might say "SS 304 is similar to SS 316L" or skip a check. The deterministic engine:

- Never skips a check
- Never hallucinates a PASS
- Reports UNVERIFIED when evidence is missing
- Produces reproducible results
- Is auditable (every check shows expected vs actual)

The LLM is only used where judgment is needed: evidence augmentation and critic review.

---

## 5. Why not let the LLM make the final decision?

Because procurement decisions must be:
- **Reproducible** — Same inputs must produce same outputs (LLMs are non-deterministic)
- **Auditable** — Every decision must show exact reasoning with evidence (LLMs can't reliably cite)
- **Honest** — Must report uncertainty rather than guess (LLMs tend to confabulate)
- **Testable** — Must pass ground-truth benchmarks (LLMs vary between runs)

The system architecture uses LLMs only in non-critical paths:
- Evidence augmentation (enriching search results) — errors here are caught by verification
- Critic review (flagging risks) — operates AFTER deterministic checks, can only block, not override

The final PASS/FAIL/WARNING/UNVERIFIED verdict is always deterministic.

---

## 6. How does the Critic work?

The Critic node runs after deterministic verification and receives:
- All verification checks (PASS/FAIL/WARNING/UNVERIFIED)
- Evidence counts and source documents
- Supplier scores and ranking

It applies rule-based checks:
- Flags cases where mandatory requirements are UNVERIFIED
- Identifies risks (e.g., no certification evidence, contradictory prices)
- Can block a recommendation if critical issues are found
- Produces a structured critic verdict (PASS/WARN/FAIL)

The Critic is the safety net — if deterministic extraction missed something, the Critic catches it. It cannot override a FAIL into a PASS, but it can block a PASS into an abstention.

---

## 7. How is evidence provenance maintained?

Every extraction and verification step records:
- `document_id` and `document_name` — Which file the evidence came from
- `page` — Page number within the document
- `text` — Raw text snippet that was used
- `method` — How it was extracted (heuristic, LLM, etc.)
- `section` — Which field the evidence supports (material, price, etc.)

This provenance flows through the entire pipeline:
- Extraction records provenance for each extracted field
- Verification checks link to the evidence IDs that support the verdict
- The frontend displays evidence with document name, page, and text snippet
- The audit trail preserves the full provenance chain

---

## 8. How does citation validation work?

Each verification check includes `evidence_ids` — references to the document chunks that justify the verdict. The Critic validates that:
- Claims have at least one supporting evidence
- Evidence values match the claim (e.g., the price in the evidence matches the comparison)
- No contradictory evidence exists for the same claim

If evidence is missing or contradictory, the check is marked UNVERIFIED or WARNING, and the Critic flags it.

---

## 9. How does procurement memory work?

The memory system maintains a vector-indexed database of past procurement cases with:
- Supplier performance (pass/fail rates, score trends)
- Compliance patterns (which requirement types fail most often)
- Price history (historical pricing for similar items)
- Case outcomes (what was recommended and why)

When a new case is evaluated, memory retrieval:
1. Finds similar past cases using vector similarity
2. Identifies the same suppliers in historical context
3. Surfaces repeated compliance issues
4. Provides price trend context

Memory enriches the current evaluation with historical context but **never overrides** current evidence. If a supplier passed last time but fails today, the system recommends against them.

---

## 10. How does memory avoid overriding current evidence?

Memory is read-only during verification. The evaluation pipeline:
1. Runs deterministic verification against current documents first
2. Retrieves memory as supplementary context only
3. Memory results appear in the "Historical Context" panel in the UI
4. The recommendation is based on current verification results, not memory

Memory can surface warnings ("this supplier failed certification checks in 3 past cases") but cannot change a current PASS to a FAIL or vice versa. The deterministic verdict is final.

---

## 11. Why MCP?

MCP (Model Context Protocol) provides a standard interface for external AI agents to interact with the procurement system. Instead of building custom integrations for every AI framework, MCP tools expose a consistent API:

- Any MCP-compatible agent can query cases, run analyses, and retrieve results
- Tools are self-describing (name, description, input schema)
- The same tools power the CLI, the API, and any MCP-connected agent

This makes the system composable — it can be used as a verification backend for larger AI workflows.

---

## 12. What MCP tools exist?

10 tools:
1. `get_case` — Retrieve case details
2. `list_case_documents` — List documents for a case
3. `search_evidence` — Vector search across documents
4. `get_supplier` — Supplier details
5. `get_supplier_history` — Historical performance
6. `get_requirement` — Requirement specifications
7. `evaluate_supplier` — Run verification for one supplier
8. `run_case_analysis` — Execute full 12-node pipeline
9. `get_case_decision` — Final recommendation
10. `get_audit_report` — Complete audit trail

---

## 13. How is case isolation enforced?

Each case has a unique UUID-based ID. Documents, requirements, bids, evaluations, and memory entries are all scoped to a case ID. The API routes enforce case ID validation with pattern `^[A-Za-z0-9_-]{1,128}$`. Database queries always filter by case_id. The ingestion service uses per-case SHA256 deduplication — identical content in different cases is allowed.

---

## 14. How is supplier isolation enforced?

Each supplier has a unique UUID-based ID generated during ingestion. Supplier IDs are scoped to their parent case. API routes validate supplier ID format. Memory entries reference supplier IDs and are queried within case context. The evaluation engine processes suppliers independently — one supplier's verification does not affect another's.

---

## 15. How are contradictory documents handled?

When multiple documents contain conflicting information for the same field:
1. The extraction phase deduplicates by (field, value) within a case
2. The verification engine checks each supplier's bid independently
3. The Critic identifies contradictions (e.g., two quotes from the same supplier with different prices)
4. Contradictory evidence produces UNVERIFIED status, not a guess

The adversarial evaluation includes a "contradictory_evidence" scenario that verifies this behavior — the system correctly abstains (UNVERIFIED) rather than picking one version.

---

## 16. How does abstention work?

When the system cannot verify a requirement:
1. The deterministic check produces `CheckStatus.UNVERIFIED`
2. The check deducts 12 points from the supplier score
3. The check appears in `unknowns` in the recommendation
4. If mandatory requirements are UNVERIFIED, the supplier is not recommendable
5. The system reports "insufficient evidence" rather than guessing

This is a deliberate design choice: a wrong PASS is worse than an honest abstention. The adversarial evaluation tests this with expired certifications, missing evidence, and contradictory documents.

---

## 17. How was the system evaluated?

Three evaluation dimensions:

1. **Benchmark (55 cases)** — Pre-defined cases with ground-truth expected recommendations. Measures case accuracy, recommendation accuracy, bid outcome accuracy, evidence recall. All 55/55 pass with metrics at 1.0.

2. **Retrieval (275 tasks)** — Per-case document retrieval tasks with expected document lists. Measures recall@K (all expected docs retrieved) = 1.0 and recall@1 (top-ranked is expected) = 0.502.

3. **Adversarial (15 scenarios)** — Synthetic scenarios testing edge cases: wrong material, expired certifications, contradictory evidence, delivery violations. 9/15 correct, 4/15 documented limitations, 2/15 incorrect.

---

## 18. What are the limitations?

1. **Fixed extraction vocabulary** — Only 8 field types are extracted. Requirements outside these (purity %, accuracy, SLAs) are silently dropped.

2. **Delivery format sensitivity** — Regex requires "within/in/under" keywords. "Delivery: 28 days" colon format is not detected.

3. **No unit compatibility checking** — "5000 kg" and "5000 pieces" not detected as incompatible.

4. **Verbal vs documented claims** — Cannot distinguish in deterministic mode.

5. **Retrieval ordering** — All documents found (recall@K = 1.0) but not always ranked first (recall@1 = 0.502).

6. **Synthetic benchmark** — Evaluation uses generated data, not real procurement.

---

## 19. How would this scale?

Current architecture supports:
- **Horizontal scaling** — FastAPI stateless backend behind a load balancer
- **Database scaling** — PostgreSQL with connection pooling (configurable pool size/max overflow)
- **Vector scaling** — Qdrant server mode with sharding for millions of documents
- **Pipeline parallelism** — Each supplier evaluation is independent; can be parallelized within a case

For real enterprise deployment:
- Add LLM provider for evidence augmentation and Critic (currently `none` for benchmarking)
- Add document OCR for scanned PDFs (currently assumes text-extractable PDFs)
- Add multi-tenant case isolation
- Add webhook notifications for case completion
- Add batch analysis for hundreds of cases

---

## 20. What would you change for real enterprise deployment?

1. **Add LLM integration** — Enable evidence augmentation and Critic to catch issues deterministic extraction misses
2. **Add OCR support** — Handle scanned documents with Tesseract or cloud OCR
3. **Add real embedding model** — Replace hash embeddings with sentence-transformers for better retrieval precision
4. **Add multi-tenancy** — Case isolation per organization with role-based access
5. **Add document versioning** — Track document revisions and re-verify when documents change
6. **Add workflow automation** — Webhooks, email notifications, approval chains
7. **Add compliance templates** — Pre-built requirement templates for common procurement categories
8. **Add learning from corrections** — Allow users to correct verification results and feed back into extraction rules
