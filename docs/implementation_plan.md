# Implementation Plan

## Goal

Build the **Procurement Intelligence & Verification System** end-to-end: a multi-agent,
RAG-backed, evidence-grounded system that determines which suppliers genuinely satisfy
mandatory procurement requirements, rather than letting the cheapest quote win.

## Environment (verified)

| Tool     | Version       | Notes                                  |
|----------|---------------|----------------------------------------|
| Python   | 3.12.0        | venv at `.venv`                        |
| Node     | v24.19.0      | used for React frontend                |
| Docker   | 29.2.0        | used for compose (postgres/qdrant)     |
| Ollama   | 0.34.0        | qwen2.5:1.5b · qwen2.5:0.5b available   |

## Core principles that shape the implementation

1. **Deterministic-first.** All decisions that can be computed (arithmetic, price,
   quantity, delivery days, material matching, certification validity, scoring) live in a
   pure `VerificationEngine`. LLMs never "decide" PASS/FAIL.
2. **Every claim carries evidence.** Extraction writes `Evidence` rows with
   document/page/section/span so every conclusion is traceable.
3. **Abstention is a feature.** Missing/insufficient evidence → "INSUFFICIENT EVIDENCE",
   never a guess.
4. **LLM is optional.** `LLMProvider` abstraction (Local/Ollama · API · deterministic
   fallback). The whole pipeline runs end-to-end without any LLM so tests and the
   benchmark are deterministic.
5. **No cheap-wins.** A supplier is shortlisted only if all mandatory checks pass.

## Phases

- **Phase 1 — Skeleton.** Config (pydantic-settings), SQLAlchemy models, SQLite default
  (PostgreSQL in Docker), migration runner, FastAPI app, `/api/health`. *(in progress)*
- **Phase 2 — Verification engine.** Pure deterministic matcher: material, quantity,
  price, delivery, certifications (incl. validity), units, currencies, tolerances,
  scoring, rejection reasons. Heavy unit tests.
- **Phase 3 — Benchmark generator.** reportlab-based generator producing RFQ/quote/spec/
  policy/certificate PDFs for 20+ hardened cases; manifest with expected outcomes.
- **Phase 4 — PDF pipeline.** Ingestion, page text + table extraction (pypdf/pdfplumber),
  safe upload, storage, Evidence model.
- **Phase 5 — RAG.** Pluggable embeddings (Ollama / hash fallback), Qdrant (server or
  in-memory), chunking with metadata, hybrid vector+BM25 retrieval, RRF fusion, rerank.
- **Phase 6 — Extraction agents.** Requirement Analyzer + Document Extraction Agent
  (heuristics first, LLM enrichment optional, provenance preserved).
- **Phase 7 — Orchestration.** LangGraph `StateGraph`: Planner → fan-out extraction →
  verification → decision → critic → report. Structured outputs throughout.
- **Phase 8 — Policy + Supplier Analysis agents.** Evidence-only conclusions, memory
  lookups for historical price/performance.
- **Phase 9 — Critic.** Independent claim-vs-evidence audit, citation validation,
  arithmetic recheck, abstention thresholds.
- **Phase 10 — Decision engine.** Rank valid suppliers by score; never override
  deterministic failures; produce the explainable final report.
- **Phase 11 — MCP tools.** `search_procurement_history`, `search_supplier_history`,
  `retrieve_document`, `search_evidence`, `validate_requirement`,
  `compare_supplier_quotes`, `check_certification` with schemas + JSON-RPC surface.
- **Phase 12 — Memory.** Persistent, scoped procurement memory (prices, performance,
  decisions, recurring materials) written only from evidence.
- **Phase 13 — Evaluation framework.** Benchmark runner + metrics (recommendation
  accuracy, claim support, hallucination rate, citation accuracy, Recall@K, latency…).
- **Phase 14 — Observability.** Request/case tracing, agent/tool/LLM/retrieval spans,
  OTel-style spans + DB audit log + structured JSON logs.
- **Phase 15 — Frontend.** React (Vite) dashboard: create case → upload → progress →
  comparison → evidence review → recommendation → audit trail.
- **Phase 16 — Docker/docs/polish.** backend+frontend+postgres+qdrant compose, README,
  `docs/*`, final integration tests.

## Definition of done

The system runs locally (and in Docker), produces extracted requirements + supplier
comparisons + deterministic PASS/FAIL + evidence citations + ranked recommendation +
audit trail, and passes the full automated test suite and benchmark.