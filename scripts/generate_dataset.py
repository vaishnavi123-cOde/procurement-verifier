"""Generate the full synthetic benchmark dataset.

Regression target:

* ``data/synthetic/`` — materials, suppliers, meta + ``cases/<case_id>/`` with
  ``manifest.json`` and the rendered PDF documents (RFQ, spec, policy, quotes,
  certificates, history).
* ``data/benchmark/`` — ``index.json`` + ``cases/<case_id>.json`` ground truth.
* ``data/raw/PROVENANCE.json``, ``data/processed/reference/market_prices.json``.

The output is deterministic for a given seed; generation never reads the
production decision engine.

Usage:
    python scripts/generate_dataset.py [data_root]
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from backend.app.dataset.benchmark import (  # noqa: E402
    build_benchmark,
    generate_synthetic,
    validate_benchmark,
)


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else REPO_ROOT / "data"
    print(f"data root: {root}")

    generate_synthetic(root)
    print("synthetic layer written:", sorted(p.name for p in (root / "synthetic").iterdir()))

    build_benchmark(root)
    index_f = root / "benchmark" / "index.json"
    print("benchmark index written:", index_f)

    errors = validate_benchmark(root)
    if errors:
        print(f"VALIDATION FAILED ({len(errors)}):")
        for e in errors:
            print("  -", e)
        return 1

    manifest_files = sorted((root / "synthetic" / "cases").glob("*/manifest.json"))
    pdf_count = sum(1 for p in (root / "synthetic" / "cases").glob("*/*.pdf"))
    print(f"OK: {len(manifest_files)} cases, {pdf_count} PDFs, benchmark valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())