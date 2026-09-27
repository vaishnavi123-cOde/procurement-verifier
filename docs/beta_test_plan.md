# Beta Test Plan

Phase 23 — External User Validation. This plan is the prepared protocol for running usability + trust/explainability sessions with external testers on the Procurement Verifier.

## Environment

Run the app locally (`docker compose up -d` or the dev servers) and open the frontend at `http://localhost:8080`. Five synthetic cases are pre-loaded and already analysed by `scripts/setup_beta_environment.py`:

| Case | Beta label | What it exercises |
|------|-----------|-------------------|
| `bench-006` | compliant | One RECOMMENDED supplier (Zenith Engineering); two rejected (price over budget, material mismatch) |
| `bench-031` | currency_fx | Foreign-currency quote accepted via FX; a foreign quote over budget; a missing-certification supplier |
| `bench-039` | abstention_insufficient | ALL suppliers UNVERIFIED (missing price/cert/field) — the system abstains |
| `bench-045` | abstention_no_valid | ALL suppliers FAIL with different reasons — no valid bid exists, no recommendation |
| `bench-050` | dual_compliant | Two PASS suppliers (ranking decision) and one expired-certificate reject |

All five cases carry the dataset tag `synthetic=true`; their documents contain `[ADVERSARIAL SECURITY TEST DATA - SYNTHETIC - DO NOT USE IN PRODUCTION]`-style demo markers and should be treated as **demo data, not real market data**.

Environment summary is recorded in `data/beta/beta_environment.json`.

## Personas

| Persona | Background | Goals |
|---------|-----------|-------|
| **Procurement Manager** | Purchasing lead; reviews recommendations before decisions; domain expert, not a developer | Trust the shortlist; understand *why* a supplier was (not) selected; know when to abstain |
| **Procurement Analyst** | Hands-on evaluator; runs tenders; expects traceable evidence | Verify each requirement against the evidence; reproduce the decision; compare suppliers |
| **Technical Reviewer** | IT/QA reviewer (developer-adjacent) | Audit model correctness, evidence grounding, execution internals, error handling |

## Session Protocol

- **Do not guide the tester.** Only clarify task wording (never the answer). Note anything the tester asks about or gets stuck on.
- **Record, don't fix.** UX issues observed during a session are logged immediately into `docs/beta_feedback.md` under the CRITICAL/HIGH/MEDIUM/LOW categories. No remediation happens mid-session or mid-collection phase.
- **Measure time-on-task** per task (start → completion or abandonment), and the navigation path used.
- Each session targets **12 tasks across the 5 cases,** ~2–3 tasks per case, in a fixed order that starts easy and gets harder.

## The 12 Tasks

| # | Task | Case | What we observe |
|---|------|------|-----------------|
| 1 | Open case `bench-006` and find the recommended supplier. | bench-006 | Time to decision; where they looked; did they understand the Recommendation panel |
| 2 | Explain why the other two suppliers were rejected. | bench-006 | Do they find the reasons (price over / material mismatch)? From which panel (Why this decision / matrix / comparison)? |
| 3 | Find the evidence supporting the minimum quantity requirement. | bench-006 | Can they reach the evidence snippet from the Requirement matrix? Is the expected-vs-actual cell clear? |
| 4 | Verify whether the material requirement is satisfied by **Quantum Industries**. | bench-006 | UNVERIFIED/FAIL cell interpretation; do they detect the material mismatch (MS vs HDPE)? |
| 5 | Verify whether the delivery-time requirement is satisfied by **Zenith Engineering**. | bench-006 | Delivery ≤ 21 days vs quote 16 days — can they confirm the check? |
| 6 | Confirm the certification evidence for **ISO 9001** in case `bench-006`. | bench-006 | Certificate document discoverability; expiry handling. |
| 7 | Judge: is the recommendation fully supported by the evidence? | bench-050 | Trust calibration; do they inspect the Critic panel, supported/unsupported claims? |
| 8 | Explain what the Critic (evidence-grounding) step said. | bench-050 | Terminology comprehension: "Critic", "Blocked", "Evidence coverage". |
| 9 | In case `bench-031`, what historical context exists about **Orion Industries**? | bench-031 | Find Historical context panel; can they separate historical vs current evidence? |
| 10 | In case `bench-039`, explain what the system concluded and why it did **not** recommend any supplier. | bench-039 | Abstention comprehension: missing info enumerated? which documents needed? |
| 11 | In case `bench-045`, review the execution trace and identify the slowest step. | bench-045 | Can they reach AuditView; read node names/timings; raw lower-level terms. |
| 12 | Explain the recommendation for `bench-050` in your own words to a colleague. | bench-050 | Overall comprehension synthesis; what was missed or mis-stated. |

## Data Collection Sheet (per session)

- Session id, persona, date, environment
- Per task: completed / abandoned, time-on-task, confusion points, questions asked, navigation path
- Per task: at least one verbatim tester quote ("what you don't understand" is gold)
- Global: SUS-style sentiment 1–5 per panel (Recommendation / Requirement matrix / Why this decision / Supplier comparison / Critic / Historical context / Execution / Evidence)

## Definition of Done

- [ ] 3–5 cases loaded and labelled (completed: `data/beta/beta_environment.json`)
- [ ] 12-task protocol documented (this file)
- [ ] Feedback questionnaire documented (`docs/beta_feedback.md`)
- [ ] UX issues log opened with CRITICAL/HIGH/MEDIUM/LOW categories
- [ ] Terminology + evidence/abstention/trust audit recorded (`docs/user_validation_report.md`)
- [ ] No fixes applied during collection; fixes only after validated observations
- [ ] Regression preserved: pytest, 55-case benchmark, retrieval, security, frontend build