# Beta Feedback Questionnaire

Phase 23 — External User Validation. Structured questions + UX issue log. One questionnaire per session; issues are also logged here by category so they can be fixed later (collection first, fixes second).

## Section A — Post-session questionnaire (12 questions)

Answer with 1 (strongly disagree) → 5 (strongly agree) plus a note.

1. The recommendation shown for a case was easy to understand.
   *(Recommendation panel — bench-006/bench-050)*

2. The reasons behind the decision were clear (why this supplier, why not the others).
   *(Why this decision / Supplier comparison — bench-006)*

3. I could find the evidence supporting any requirement claim quickly.
   *(Requirement matrix + evidence snippets)*

4. I understood why individual suppliers were marked PASS / FAIL / UNVERIFIED.
   *(Requirement matrix, cell reasons)*

5. I could tell apart **current-case evidence** vs **historical context** about a supplier.
   *(Current evidence panels vs Historical context panel — bench-031)*

6. I understood what the Critic (evidence-grounding) step checked and its result.
   *(Critic panel — bench-050)*

7. When the system made **no recommendation**, I understood what information was missing and what document was needed.
   *(Decision/abstention panel — bench-039)*

8. The supplier comparison table helped me see differences at a glance.
   *(Supplier comparison — bench-050)*

9. Nothing about the terminology confused me (material, certification, delivery, evidence, normal language preferred).
10. Nothing I needed was missing from the case view.
11. Nothing displayed was unnecessary or distracting.
12. I could use this tool to make a purchasing decision **without a developer present**.

## Section B — UX issue classification

Only **observed** issues are logged. Timestamp, persona, and the task where it occurred are required. Classification:

| Severity | Meaning |
|----------|---------|
| CRITICAL | Abandons the task or produces a wrong outcome (e.g., tester cannot tell which supplier is approved, or believes an UNVERIFIED supplier passed) |
| HIGH | Major friction or significant misunderstanding on a primary task |
| MEDIUM | Noticeable confusion that was worked around |
| LOW | Cosmetic/terminology polish |

Analysis rule: **collect first, fix later.** No issue from a session is fixed until the collection phase is closed and the observation is agreed reproducible.

## Section C — Issue log

| Date | Severity | Persona | Task | Observed problem | Proposed fix / note |
|------|----------|---------|------|------------------|---------------------|
|      |          |         |      |                  |                     |

## Section D — Per-panel sentiment

| Panel | 1 (poor) | 2 | 3 | 4 | 5 (great) | Notes |
|-------|---------|---|---|---|---|-----------|-------|
| Recommendation | | | | | | |
| Why this decision | | | | | | |
| Requirement matrix | | | | | | |
| Supplier comparison | | | | | | |
| Critic | | | | | | |
| Historical context | | | | | | |
| Execution (audit) | | | | | | |
| Evidence | | | | | | |