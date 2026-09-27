"""Compare retrieval legs (vector-only / BM25-only / hybrid) over the benchmark.

Runs the same authored retrieval tasks as ``evaluate_retrieval.py`` but reports
document-level Recall@1/@3/@5/@10 and full-hit rate for each leg independently:

- vector-only:  order by cosine score from the embedding store
- bm25-only:    order by BM25 score over the case chunks
- hybrid:       reciprocal rank fusion of both (the production path)

Metrics are document-level (unique documents in the top-N chunk window), exactly
like the benchmark. This is analysis only — it changes nothing in the pipeline.

Usage:
    python scripts/compare_retrieval_legs.py [--data DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

K_VALUES = (1, 3, 5, 10)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare retrieval legs over the benchmark tasks.")
    p.add_argument("--data", type=Path, default=None, help="data root (default: <repo>/data)")
    return p.parse_args()


def _select_case_ids(args) -> list[str]:
    from backend.app.dataset.benchmark import case_bank
    from backend.app.dataset.scenarios import build_manifest

    return [build_manifest(s).case_id for s in case_bank()]


def _doc_recall(docs: list[str], expected: set[str], k: int) -> int:
    return len(set(docs[:k]) & expected)


def main() -> int:
    args = _parse_args()
    data_root = (args.data or REPO_ROOT / "data").resolve()
    runs_dir = data_root / "evaluation_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    os.environ["DATABASE_URL"] = f"sqlite:///{(runs_dir / 'retrieval_legs_eval.db').as_posix()}"
    os.environ["UPLOAD_DIR"] = str(runs_dir / "retrieval_legs_uploads")
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

    def _run_case(db, case_id: str) -> dict | None:
        manifest_file = data_root / "benchmark" / "cases" / f"{case_id}.json"
        manifest = from_json(manifest_file.read_text(encoding="utf-8"))
        case_dir = data_root / "synthetic" / "cases" / case_id
        try:
            case = store.create_case(
                db, name=manifest.title, description=f"legs {case_id}",
                metadata_json={"case_date": manifest.case_date.isoformat(), "benchmark": True},
            )
            db.commit()
            for pdf in sorted(case_dir.glob("*.pdf")):
                ingest_document(db, case.id, pdf.name, pdf.read_bytes())
            db.commit()

            vs = VectorStore.from_settings()
            retriever = HybridRetriever(vs, db=db)
            index_case_documents(db, case.id, vs)
            filenames = {doc.id: doc.filename for doc in store.list_documents(db, case.id)}
            # unfiltered case BM25 = the pure BM25 leg (no doc-type inference)
            bm25, bm25_rows = retriever._get_bm25(case.id, None)

            tasks: list[dict] = []
            for task in manifest.retrieval_tasks:
                expected = set(task.expected_doc_ids)
                top_k = 10

                # vector-only
                vec_ranked = vs.search(task.query, case_id=case.id, top_k=top_k * 2)
                vec_docs = list(dict.fromkeys(
                    filenames.get(p.get("document_id", ""), "") for p, _ in vec_ranked
                ))
                # bm25-only
                idxs = bm25.search(task.query, top_k=top_k * 2)
                bm_docs = list(dict.fromkeys(
                    filenames.get(bm25_rows[i].document_id, "") for i in idxs if i < len(bm25_rows)
                ))
                # hybrid
                hits = retriever.retrieve(task.query, case_id=case.id, top_k=top_k)
                hyb_docs = list(dict.fromkeys(filenames.get(h.document_id, "") for h in hits))

                def stats(docs: list[str]) -> dict:
                    return {
                        f"recall@{k}": _doc_recall(docs, expected, k) for k in K_VALUES
                    } | {"full_hit": 1 if len(set(docs[:10]) & expected) == len(expected) else 0,
                         "n_expected": len(expected)}

                tasks.append({
                    "id": task.id, "query": task.query, "expected": sorted(expected),
                    "vector": stats(vec_docs), "bm25": stats(bm_docs), "hybrid": stats(hyb_docs),
                })
            vs.close()
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            return None
        return {"case_id": case_id, "tasks": tasks}

    per_case: list[dict] = []
    with SessionLocal() as db:
        for cid in _select_case_ids(args):
            result = _run_case(db, cid)
            if result is not None:
                per_case.append(result)
            else:
                print(f"  {cid}: ERROR")

    all_tasks = [t for c in per_case for t in c["tasks"]]
    n_tasks = max(1, len(all_tasks))

    def aggregate(key: str) -> dict:
        agg = {f"recall@{k}_or_more": 0.0 for k in K_VALUES}
        full = 0
        raw = {f"recall@{k}": 0 for k in K_VALUES}
        for t in all_tasks:
            s = t[key]
            for k in K_VALUES:
                raw[f"recall@{k}"] += s[f"recall@{k}"] / max(1, s["n_expected"])
            full += s["full_hit"]
        return {
            f"recall@{k}": round(raw[f"recall@{k}"] / n_tasks, 4) for k in K_VALUES
        } | {"full_hit_tasks": f"{full}/{len(all_tasks)}"}

    report = {
        "legs": {leg: aggregate(leg) for leg in ("vector", "bm25", "hybrid")},
        "tasks": all_tasks,
    }
    report["logged_hardest"] = _hardest(all_tasks)
    report_path = runs_dir / "retrieval_legs_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"\n--- retrieval legs report ({len(all_tasks)} tasks) ---")
    for leg in ("vector", "bm25", "hybrid"):
        a = report["legs"][leg]
        print(f"  {leg:8s}: " + ", ".join(f"{k}={v}" for k, v in a.items()))
    print("\nhardest tasks (full-hit failure on every leg):")
    for h in report["logged_hardest"]:
        print(f"  {h['query'][:50]} expected={h['expected']}")
    print(f"report written: {report_path}")
    return 0


def _hardest(all_tasks: list[dict]) -> list[dict]:
    hard = [t for t in all_tasks if not (t["vector"]["full_hit"] or t["bm25"]["full_hit"]
                                         or t["hybrid"]["full_hit"])]
    return hard[:15]


if __name__ == "__main__":
    raise SystemExit(main())