"""RAG retrieval evaluation: Recall@K of the hybrid retriever over authored tasks.

For every benchmark case the generated PDFs are ingested and indexed (chunks +
hash embeddings + BM25), then each ``retrieval_tasks`` query is run through the
same ``HybridRetriever`` the pipeline uses. Retrieval is scored against the
authored ``expected_doc_ids`` (filenames) without feeding ground truth in.

Metrics reported:
- recall@K (K = expected hits) overall and per case
- recall@1 (does the top hit come from an expected document)
- full-hit share (cases/tasks with perfect recall)

Usage:
    python scripts/evaluate_retrieval.py [--data DIR] [--cases a,b,c]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the retrieval benchmark over synthetic cases.")
    p.add_argument("--data", type=Path, default=None, help="data root (default: <repo>/data)")
    p.add_argument("--cases", type=str, default="", help="comma-separated case ids or a range a:b")
    return p.parse_args()


def _select_case_ids(args) -> list[str]:
    from backend.app.dataset.benchmark import case_bank
    from backend.app.dataset.scenarios import build_manifest

    all_ids = [build_manifest(s).case_id for s in case_bank()]
    selected = list(all_ids)
    if args.cases:
        tokens = [t.strip() for t in args.cases.split(",") if t.strip()]
        if any(":" in t for t in tokens):
            def idx(cid: str) -> int:
                return int(cid.rsplit("-", 1)[-1])

            bounds: list[int] = []
            for t in tokens:
                if ":" in t:
                    a, b = (int(x) for x in t.split(":", 1))
                    bounds += [a, b]
                else:
                    bounds += [int(t), int(t)]
            selected = []
            for i, (a, b) in enumerate(zip(bounds[::2], bounds[1::2])):
                selected += [c for c in all_ids if a <= idx(c) <= b]
        else:
            selected = [c for c in all_ids if any(c.endswith(t) or t in c for t in tokens)]
    return selected


def _run_case(db, store, ingest_document, index_case_documents, from_json,
              retriever_factory, data_root: Path, case_id: str) -> dict | None:
    manifest_file = data_root / "benchmark" / "cases" / f"{case_id}.json"
    manifest = from_json(manifest_file.read_text(encoding="utf-8"))
    case_dir = data_root / "synthetic" / "cases" / case_id
    try:
        case = store.create_case(
            db,
            name=manifest.title,
            description=f"synthetic benchmark case {case_id}",
            metadata_json={"case_date": manifest.case_date.isoformat(), "benchmark": True},
        )
        db.commit()
        case_id_db = case.id
        for pdf in sorted(case_dir.glob("*.pdf")):
            ingest_document(db, case_id_db, pdf.name, pdf.read_bytes())
        db.commit()

        vector_store, retriever = retriever_factory(db)
        index_case_documents(db, case_id_db, vector_store)

        filenames = {doc.id: doc.filename for doc in store.list_documents(db, case_id_db)}

        tasks: list[dict] = []
        for task in manifest.retrieval_tasks:
            expected = set(task.expected_doc_ids)
            top_k = 10
            hits = retriever.retrieve(task.query, case_id=case_id_db, top_k=top_k)
            retrieved = [filenames.get(h.document_id, "") for h in hits]
            retrieved_docs = list(dict.fromkeys(retrieved))  # dedup preserving order
            hit_set = set(retrieved_docs) & expected
            tasks.append({
                "id": task.id or task.query,
                "query": task.query,
                "expected": sorted(expected),
                "retrieved": retrieved_docs,
                "recall_at_k": round(len(hit_set) / len(expected), 4) if expected else 1.0,
                "hit_at_1": 1 if (retrieved_docs and retrieved_docs[0] in expected) else 0,
                "full_hit": 1 if len(hit_set) == len(expected) else 0,
            })
        vector_store.close()
    except Exception as exc:  # noqa: BLE001 - evaluator must not crash on one case
        import traceback
        traceback.print_exc()
        return None

    recall = sum(t["recall_at_k"] for t in tasks) / len(tasks) if tasks else 0.0
    return {
        "case_id": case_id,
        "title": manifest.title,
        "tags": manifest.tags,
        "n_tasks": len(tasks),
        "n_expected": sum(len(t["expected"]) for t in tasks),
        "recall_at_k": round(recall, 4),
        "recall_at_1": round(sum(t["hit_at_1"] for t in tasks) / len(tasks), 4) if tasks else 0.0,
        "full_hit": 1 if tasks and all(t["full_hit"] for t in tasks) else 0,
        "tasks": tasks,
    }


def main() -> int:
    args = _parse_args()
    data_root = (args.data or REPO_ROOT / "data").resolve()
    runs_dir = data_root / "evaluation_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    os.environ["DATABASE_URL"] = f"sqlite:///{(runs_dir / 'retrieval_eval.db').as_posix()}"
    os.environ["UPLOAD_DIR"] = str(runs_dir / "retrieval_uploads")
    os.environ["VECTOR_STORE_MODE"] = "memory"
    os.environ["EMBEDDING_PROVIDER"] = "hash"

    from backend.app.database.engine import SessionLocal, init_db
    from backend.app.dataset.manifests import from_json
    from backend.app.rag.hybrid import HybridRetriever
    from backend.app.rag.vector_store import VectorStore
    from backend.app.repositories import store
    from backend.app.services.indexing import index_case_documents
    from backend.app.services.ingestion import ingest_document

    init_db()

    case_ids = _select_case_ids(args)
    print(f"evaluating retrieval on {len(case_ids)} cases")

    def retriever_factory(db):
        vs = VectorStore.from_settings()
        return vs, HybridRetriever(vs, db=db)

    per_case: list[dict] = []
    with SessionLocal() as db:
        for cid in case_ids:
            result = _run_case(db, store, ingest_document, index_case_documents,
                               from_json, retriever_factory, data_root, cid)
            if result is None:
                print(f"  {cid}: ERROR (see above)")
                continue
            per_case.append(result)

    n_cases = max(1, len(per_case))
    all_tasks = [t for c in per_case for t in c["tasks"]]
    n_tasks = len(all_tasks)
    n_expected = sum(len(t["expected"]) for t in all_tasks)
    hit_at_k = sum(len(set(t["retrieved"]) & set(t["expected"])) for t in all_tasks)
    recall1 = sum(t["hit_at_1"] for t in all_tasks)
    full_cases = sum(c["full_hit"] for c in per_case)
    full_tasks = sum(t["full_hit"] for t in all_tasks)

    metrics = {
        "retrieval_recall_at_k": hit_at_k / max(1, n_expected),
        "recall_at_1": recall1 / max(1, n_tasks),
        "full_hit_cases": f"{full_cases}/{len(per_case)}",
        "full_hit_tasks": f"{full_tasks}/{n_tasks}",
    }

    print(f"\n--- retrieval report ({len(per_case)} cases, {n_tasks} tasks, {n_expected} expected docs) ---")
    for key, value in metrics.items():
        print(f"  {key}: {value}")
    print("\ncases below perfect Recall@K:")
    for c in per_case:
        if c["recall_at_k"] < 1.0:
            misses = []
            for t in c["tasks"]:
                if t["recall_at_k"] < 1.0:
                    misses.append(f"{t['query'][:40]} -> {[f for f in t['expected'] if f not in t['retrieved']]}")
            print(f"  {c['case_id']} recall={c['recall_at_k']}")
            for m in misses:
                print(f"      {m}")

    report_path = runs_dir / "retrieval_report.json"
    report_path.write_text(json.dumps({
        "dataset": "procurement-benchmark",
        "embedding_provider": "hash",
        "cases": per_case,
        "metrics": {k: v for k, v in metrics.items() if k != "full_hit_cases"},
    }, indent=2), encoding="utf-8")
    print(f"report written: {report_path}")
    return 0 if full_cases == len(per_case) else 2


if __name__ == "__main__":
    raise SystemExit(main())