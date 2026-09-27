# Project Summary

## Evidence-Backed Procurement Decision & Verification System

A production-grade procurement compliance system that evaluates supplier bids against RFQ requirements using a deterministic verification engine, hybrid RAG retrieval, and 12-node agent orchestration — with full evidence provenance, audit trails, and an honest abstention principle.

---

## Engineering Contribution

This is not a chatbot wrapper or a simple document Q&A system. It is a full-stack decision-support system with:

### Deterministic Verification Engine
Every PASS/FAIL/WARNING/UNVERIFIED decision uses pure arithmetic and exact string matching — never an LLM. Material grades are compared exactly (SS 304 ≠ SS 316L). Prices are compared with explicit currency conversion. Quantities are checked with operators (gte, lte). Certifications are validated against expiry dates. When evidence is missing, the system reports UNVERIFIED rather than guessing.

### Evidence Provenance & Citations
Every verification check links to the exact source document, page number, and text snippet that justifies the verdict. No black-box decisions. Click any check result to see the evidence.

### Hybrid RAG Retrieval
Combines Qdrant vector similarity search with BM25 keyword retrieval. Results are scored and merged, providing both semantic understanding and exact keyword matching for procurement-specific terminology.

### 12-Node Agent Orchestration
LangGraph sequential pipeline with structured state passing between nodes: case loading → document discovery → requirement analysis → extraction → evidence → memory → evaluation → verification → RAG → critic → decision → memory write.

### Procurement Memory
Tracks historical supplier performance, repeated compliance issues, and price trends. Memory enriches current evaluations but never overrides current evidence — a supplier that passed last time can fail today.

### Critic Node
Reviews all deterministic verification results against evidence, flags risks (e.g., unverified mandatory requirements), and can block false PASS recommendations.

### MCP Integration
10 Model Context Protocol tools for external AI agent integration, enabling other systems to query cases, run analyses, and retrieve audit reports programmatically.

### Observability
Structured JSON logging with correlation IDs, request tracing, and OpenTelemetry-compatible endpoints. Every pipeline execution is traceable.

---

## Technical Highlights

| Area | Implementation |
|------|---------------|
| Verification | Deterministic arithmetic/comparison, never LLM-generated |
| Retrieval | Hybrid vector (Qdrant) + keyword (BM25) with score merging |
| Orchestration | LangGraph 12-node sequential graph |
| Memory | Supplier performance tracking with similarity scoring |
| Security | HMAC tokens, rate limiting, per-case dedup, PDF validation |
| API | FastAPI with typed path params, CORS, auth middleware |
| Frontend | React + TypeScript with verification matrix, evidence viewer |
| Deployment | Docker Compose with PostgreSQL, Qdrant, nginx |
| Evaluation | 55-case benchmark, 15 adversarial scenarios, retrieval tasks |
| Abstention | Reports UNVERIFIED when evidence insufficient, never hallucinates |

---

## Scale

- **55 benchmark cases** with 3 suppliers each (165 supplier evaluations)
- **275 retrieval tasks** across all cases
- **174 unit + integration tests**
- **23 security tests**
- **15 adversarial scenarios** testing edge cases
- **10 MCP tools** for external integration
- **72.8 ms** average pipeline latency (in-memory Qdrant, SQLite)

---

## What Makes This Different

Most AI procurement tools use LLMs to "understand" documents and make recommendations. This system uses LLMs only for evidence augmentation and critic review — the actual compliance verdict is always deterministic. This means:

1. **Reproducible** — Same inputs always produce same outputs
2. **Auditable** — Every check shows expected vs actual with citations
3. **Honest** — Reports UNVERIFIED instead of guessing when evidence is missing
4. **Testable** — 55-case benchmark with ground truth, all passing
5. **Scalable** — Deterministic checks are fast (72.8 ms avg) and parallelizable
