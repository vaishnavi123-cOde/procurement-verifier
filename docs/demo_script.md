# Demo Script

Step-by-step walkthrough for demonstrating the Procurement Verifier.

---

## Setup (2 minutes)

```bash
# Terminal 1 — Backend
cd procurement-verifier
python -m venv .venv
.venv\Scripts\activate
pip install -e .
python -m backend.app.main
```

Backend starts on http://0.0.0.0:8000

```bash
# Terminal 2 — Frontend
cd frontend
npm install
npm run dev
```

Frontend starts on http://localhost:5173

---

## Demo 1: Benchmark (1 minute)

```bash
# Terminal 3
python scripts/evaluate.py
```

Show output:
```
evaluating 55 cases
--- benchmark report (55 cases, 55 passed) ---
  case_accuracy: 1.0
  recommendation_accuracy: 1.0
  avg_duration_ms: 72.8
```

**Talking point:** 55 procurement cases, each with 3 suppliers, all evaluated correctly in 72.8ms average.

---

## Demo 2: Case Analysis UI (3 minutes)

Navigate to http://localhost:5173 in browser.

1. **Case Selection** — Select bench-006 (HDPE pipe procurement)
2. **Requirements** — Show 5 requirements: material (HDPE), quantity (≥4879m), price (≤₹12,18,500), delivery (≤21 days), ISO 9001:2015
3. **Supplier Comparison** — Table showing 3 suppliers with scores:
   - Zenith Engineering: PASS (score 100)
   - Meridian Industries: FAIL (score 80) — price exceeds budget
   - Quantum Industries: FAIL (score 80) — wrong material (MS ≠ HDPE)
4. **Verification Matrix** — Grid showing each requirement × each supplier with PASS/FAIL/WARNING
5. **Evidence** — Click any check to see source document, page, text snippet
6. **Why This Decision** — Reasons why Zenith was recommended
7. **Critic** — Risk assessment and blocking issues
8. **Historical Context** — Past supplier performance (empty for fresh cases)
9. **Execution Trace** — 12 nodes with timing bars

---

## Demo 3: Adversarial Robustness (1 minute)

```bash
python scripts/adversarial_evaluation.py
```

Show key scenarios:
- **wrong_material** — FAIL (SS 304 ≠ SS 316L) ✓
- **expired_certification** — UNVERIFIED (abstains rather than guesses) ✓
- **contradictory_evidence** — UNVERIFIED (detects conflict, abstains) ✓

**Talking point:** When evidence is insufficient, the system abstains rather than hallucinating. This is a deliberate design choice for procurement where wrong PASS recommendations have real financial consequences.

---

## Demo 4: MCP Tools (1 minute)

```bash
python -m backend.app.mcp
```

Show 10 registered tools. Explain that any MCP-compatible AI agent can:
- Query cases and documents
- Run analyses
- Retrieve audit reports
- Search evidence

**Talking point:** The system is composable — it can be used as a verification backend for larger AI workflows.

---

## Demo 5: Security (30 seconds)

```bash
python -m pytest tests/unit/test_security.py -q
```

Show 23/23 passed. Explain:
- HMAC token authentication (optional)
- Rate limiting
- Per-case document deduplication
- PDF magic-byte validation
- No secrets committed

---

## Key Messages

1. **Deterministic verdicts** — Every PASS/FAIL uses arithmetic, never LLM
2. **Evidence provenance** — Every check links to source document
3. **Honest abstention** — Reports UNVERIFIED when evidence insufficient
4. **Full audit trail** — Every decision is traceable
5. **72.8ms latency** — Fast enough for real-time use
6. **55/55 benchmark** — All cases pass with ground truth
