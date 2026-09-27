"""Performance / observability report over the recorded audit trail.

Aggregates the actual persisted observability data (AgentExecution rows,
RetrievalLog stats with the metadata column, ToolCall / LLMCall / ExecutionSpan)
into a per-case performance summary, without re-running any analysis.

Usage:
    python scripts/performance_report.py               # all recorded cases
    python scripts/performance_report.py --case bench-006
    python scripts/performance_report.py --limit 5 --json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Report recorded analysis performance/observability data.")
    p.add_argument("--case", type=str, default="", help="restrict to one case id (DB id or bench id)")
    p.add_argument("--limit", type=int, default=20, help="max cases to report (default 20)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of the text table")
    return p.parse_args()


def _stats(values) -> dict:
    values = [v for v in values if v is not None and v != 0]
    if not values:
        return {"count": 0, "avg": 0.0, "max": 0.0, "total": 0.0}
    return {
        "count": len(values),
        "avg": round(statistics.fmean(values), 1),
        "max": round(max(values), 1),
        "total": round(sum(values), 1),
    }


def _build_report(db, cases, *, store) -> list[dict]:
    report: list[dict] = []
    for case in cases:
        executions = store.list_agent_executions(db, case.id)
        if not executions:
            continue
        node_durations = [e.duration_ms for e in executions if e.duration_ms is not None]
        by_agent: dict[str, int] = {}
        for e in executions:
            key = f"{e.agent}/{e.status}"
            by_agent[key] = by_agent.get(key, 0) + 1

        retr = store.list_retrieval_logs(db, case.id)
        retrieval_stats = {
            "vector_hits": _stats([r.metadata_json.get("vector_hits", 0) for r in retr]),
            "bm25_hits": _stats([r.metadata_json.get("bm25_hits", 0) for r in retr]),
            "hybrid_hits": _stats([r.metadata_json.get("hybrid_hits", 0) for r in retr]),
            "latency_ms": _stats([r.latency_ms for r in retr]),
        }
        tools = store.list_tool_calls(db, case.id)
        llms = store.list_llm_calls(db, case.id)
        mcp_calls = [t for t in tools if t.agent == "mcp"]

        report.append({
            "case_id": case.id,
            "name": case.name,
            "status": case.status,
            "node_runs": len(executions),
            "node_duration_ms": _stats(node_durations),
            "node_status_counts": by_agent,
            "retrieval_queries": len(retr),
            "retrieval": retrieval_stats,
            "tool_calls": len(tools),
            "mcp_tool_calls": len(mcp_calls),
            "llm_calls": len(llms),
            "spans": len(store.list_spans(db, case.id)),
        })

    report.sort(key=lambda r: r["node_duration_ms"]["total"], reverse=True)
    return report


def _print_text(report: list[dict], limit: int) -> None:
    print(f"{'case_id':<26} {'nodes':>5} {'avg_ms':>7} {'max_ms':>7} {'retr_q':>6} "
          f"{'v_hits':>6} {'b_hits':>6} {'mcp':>4} {'llm':>4} {'spans':>6}")
    for row in report[:limit]:
        r = row["retrieval"]
        print(f"{row['case_id']:<26} {row['node_runs']:>5} {row['node_duration_ms']['avg']:>7} "
              f"{row['node_duration_ms']['max']:>7} {row['retrieval_queries']:>6} "
              f"{r['vector_hits']['avg']:>6} {r['bm25_hits']['avg']:>6} "
              f"{row['mcp_tool_calls']:>4} {row['llm_calls']:>4} {row['spans']:>6}")
    if report:
        total = _stats([r["node_duration_ms"]["total"] for r in report])
        print(f"\ncases reported: {len(report)} | avg pipeline ms: {total['avg']} | "
              f"total (node time) ms: {total['total']}")


def main() -> None:
    args = _parse_args()
    from backend.app.database.engine import init_db, session_scope
    from backend.app.repositories import store

    init_db()
    with session_scope() as db:
        if args.case:
            from backend.app.services.case_loader import CaseNotFoundError, find_or_create_case

            try:
                case, _ = find_or_create_case(db, args.case)
                cases = [case]
            except CaseNotFoundError:
                case = store.get_case(db, args.case)
                if case is None:
                    raise SystemExit(f"No case found for '{args.case}'.")
                cases = [case]
        else:
            cases = store.list_cases(db, limit=max(args.limit * 4, 50))

        report = _build_report(db, cases, store=store)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_text(report, args.limit)


if __name__ == "__main__":
    main()