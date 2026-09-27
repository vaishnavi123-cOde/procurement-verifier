# User Validation Report

Phase 23 — EXTERNAL USER VALIDATION / BETA TESTING.

**Honesty statement:** no external testers participated at the time this report was written, and **no fabricated test results, usability measurements, or feedback are included anywhere in this document.** The beta environment, the 12-task test plan, and the feedback questionnaire are fully prepared and ready to run. External validation has **not yet been performed**; this report records that status explicitly.

## TEST SETUP

### Environment

- The beta environment is assembled and pre-labelled (see `data/beta/beta_environment.json`):
  - `bench-006` — **compliant** (recommended: Zenith Engineering; Meridian price-over; Quantum material-mismatch)
  - `bench-031` — **currency_fx** (Orion accepted foreign-currency bid; Pioneer over budget; Harbinger missing cert)
  - `bench-039` — **abstention_insufficient** (all suppliers UNVERIFIED: missing price / missing cert / missing field)
  - `bench-045` — **abstention_no_valid** (all suppliers FAIL: price-over, missing cert, material mismatch)
  - `bench-050` — **dual_compliant** (Nova + Harbinger PASS; Falcon expired certificate)
- Every case was loaded through the standard loader (`scripts/setup_beta_environment.py` → `find_or_create_case` / `ingest_missing_documents` / `run_orchestrated_analysis`) so documents, per-case evidence, historical-memory entries, and analysis results are present in the UI.
- All cases are synthetic (`synthetic=true`); document content is clearly labelled demo/beta data.
- The app is runnable via `docker compose up` or dev servers; frontend at `http://localhost:8080`.
- Prepared and committed, not yet executed: `docs/beta_test_plan.md` (personas + 12 tasks + observation protocol) and `docs/beta_feedback.md` (12 questions + CRITICAL/HIGH/MEDIUM/LOW issue log).

### Personas (prepared, not yet exercised)

Procurement Manager · Procurement Analyst · Technical Reviewer

## RESULTS

| Step | Status |
|------|--------|
| Beta cohort built and labelled (5 cases covering compliant / fx / abstain-insufficient / abstain-no-valid / dual) | ✅ Done |
| 12-task test plan with observation protocol | ✅ Done |
| 12-question feedback questionnaire + severity-classified issue log | ✅ Done |
| Terminology / trust / UX audit (static code review) | ✅ Done (below) |
| **External tester sessions** | ⏳ **Not yet run — no testers available at reporting time** |
| Observed time-on-task, confusion points, SUS-style sentiment | ⏳ Not measured (no sessions) — none fabricated |

## ISSUES (static audit findings — classification pending tester confirmation)

These are code-review observations from reading the current frontend. They are **candidate** issues, not confirmed user findings; per the Phase 23 rule they are collected now and **not fixed** until reproduced in a validation session.

| # | Area | Severity (candidate) | Observed (by inspection) |
|---|------|----------------------|--------------------------|
| 1 | Abstention UX | HIGH | `NoRecommendation` shows `Decision: {decisionStatus}` with the raw code (`insufficient`, `no_valid`) and a single generic sentence. It does **not** enumerate exactly which field is missing for which supplier nor which document would resolve it (spec requirement). |
| 2 | Recommendation UX | HIGH | Recommendation panel answers WHO and score/confidence but not **why the alternatives failed** at a glance; `Why this decision?` renders "No explicit decision reasoning recorded." whenever `reasons` is empty — leaving the core "why" question unanswered. |
| 3 | Trust / source labels | MEDIUM | "CURRENT EVIDENCE vs HISTORICAL CONTEXT" is only implied by panel placement; no explicit label contrast. The phrasing "ML/Analysis output vs deterministic engine" is not surfaced for the recommendation. |
| 4 | Terminology (mixed register) | MEDIUM | Panel-level copy mixes domain ("Supplier comparison", "Certification") with internal labels ("Critic", "Blocked: YES", "Citation checks", "Unsupported claims", raw decision codes) that are not explained to a Procurement Manager persona. |
| 5 | Status label inconsistency | LOW | `Decision:` header uses the code (`insufficient`/`no_valid`) while the top badge uses an uppercased label; "Blocked" renders `YES`/`No` with inconsistent casing. |
| 6 | Dev-facing detail leak | LOW | Historical context renders full raw JSON (`<pre>`) and Evidence/Audit panels show internal ids (`evidence_id.slice(0,8)`), UUIDs and node names — fine for Technical Reviewer, confusing for the other two personas. |

## FIXES

- **No product fixes have been applied in this phase.** Per Phase 23 rules, fixes require validated observations from tester sessions first.
- Two non-product changes were completed as environment/test tooling:
  - `scripts/setup_beta_environment.py` — builds and labels the beta cohort idempotently.
  - The 5 beta cases were analysed once so the UI has results + historical context.

## LIMITATIONS

- External tester sessions were **not** run: no time-on-task data, no confusion-point data, no sentiment scores exist. This is a real limitation, not a placeholder.
- The static findings above are interpretation risks; they may be confirmed or dismissed only with real sessions.
- Beta cohort uses synthetic cases only — no real procurements were used, so realistic variation in document quality/formatting remains untested externally.

## NEXT STEPS

1. Recruit ≥3 testers (one per persona), run the `docs/beta_test_plan.md` protocol.
2. Populate `docs/beta_feedback.md` issues log from sessions (collection only).
3. Confirm/reject the candidate issues above; only then apply fixes in priority order (correctness confusion → evidence discoverability → recommendation clarity → navigation → terminology → polish).
4. Re-run regression after any fix (pytest, 55-case benchmark, retrieval, security, frontend build).

## Definition of Done checklist

- [x] Beta environment with 3–5 labelled cases (compliant / non-compliant / abstention, evidence, historical context, clearly synthetic)
- [x] `docs/beta_test_plan.md` (personas, 12 tasks, observation protocol, DoD)
- [x] `docs/beta_feedback.md` (12 questions, severity-classified issue log)
- [x] Terminology + evidence-chain + recommendation/abstention UX audit recorded
- [x] Honest reporting: no fabricated numbers; external validation explicitly marked not-yet-performed
- [ ] External tester sessions run and issues collected → **NOT DONE**
- [ ] Fixes applied (observation-backed only) → **NOT DONE**