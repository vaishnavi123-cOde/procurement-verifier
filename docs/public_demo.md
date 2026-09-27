# Public Demo Guide

A ~5 minute, presenter-led walkthrough of the system for people who did not
build it. Uses the synthetic benchmark case **`bench-006`** ("Compliance
shortlist — HDPE pipe 100 mm PN6").

> **All case data is synthetic.** It was generated deterministically for
> evaluation. Supplier names (Zenith Engineering, Meridian Industries, Quantum
> Industries) are fictional.

---

## Before the demo

1. Load `bench-006` into the running app (uploads + ingestion) so the case
   shows up in the case list. Load a second case with different outcomes if you
   want to show contrast.
2. Run the analysis so an execution result exists.
3. Have the terminal visible for the execution-trace step.

---

## Script

### 0:30 — The problem (no UI)

> "A procurement team gets an RFQ, quotes from suppliers, and certificates. The
> reviewer must check material, quantity, price, delivery and certifications —
> against policy. This system does that check automatically and every result
> points to the exact document that supports it."

### 1:00 — Open the case (Task 1 of the beta plan)

Show the case list → open **bench-006**. Point out the summary:

- **Requirements extracted** from the RFQ/spec: HDPE material, ≥ 4,879 m,
  price ≤ ₹12,18,500, delivery ≤ 21 days, ISO 9001:2015.

### 2:00 — Supplier comparison (persona: Procurement Manager)

Show the supplier table. Three suppliers:

- **Zenith Engineering** — recommended. All checks green.
- **Meridian Industries** — failed. Reason: **price over** (₹17,05,900 above
  the ₹12,18,500 cap).
- **Quantum Industries** — failed. Reason: **material mismatch** (MS, not
  HDPE).

> "The company the system recommends is not the cheapest — it is the one that
> satisfies every requirement. You can see exactly which requirement each
> supplier fails."

### 3:00 — Evidence chain (persona: Procurement Analyst)

Click into a requirement row → show the **evidence** behind it: the source
document, page, and extracted value. Show the chains:

```
Requirement → Evidence → Verification → Critic → Decision
```

Switch a requirement to its **verification detail** and show the critic review
(independent check that can downgrade an over-confident result).

### 3:45 — Historical context vs current evidence (persona: Technical Reviewer)

Open the **Historical context** panel.

> "Everything here is previous procurement memory — past performance, prior
> ratings. It is separated from today's evidence and never overrides it. If
> today's quote is compliant but history is bad, you still see the pass — plus
> the history."

The UI visually separates **CURRENT EVIDENCE** (today's documents) from
**HISTORICAL CONTEXT** (memory) from **ANALYSIS OUTPUT** (what the engine
computed).

### 4:15 — Audit / trace

Show the **Audit view**: every tool call, retrieval hit, and verdict, in order,
as a timeline. Then show the terminal/execution trace so the tester sees the
same steps the engine took.

### 4:45 — What about insufficient evidence?

Demo an abstention (run a case where the quote omits delivery, or upload the
`missing_evidence` scenario): the system **abstains** — it does not guess. The
UI says *which requirement lacks evidence* and *which document would resolve
it*, rather than failing the supplier.

### 5:00 — Close

> "No step depends on an LLM. The verdict, score and trace are deterministic —
> same documents in, same answer out. The full audit trail makes the decision
> explainable to anyone."

---

## What to emphasize (when time allows)

- **Confidence is not magic** — it is a stated, verifiable measure aligned with
  the evidence, and the critic can dispute it.
- **Score is transparent** — 100 minus fixed deductions per failed/unverified
  requirement.
- **MCP access** (local, stdio) lets tools/assistants ask the same questions the
  UI answers.
- Everything above is ground truth; nothing is hallucinated.