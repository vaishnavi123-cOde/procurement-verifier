import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.app.dataset.benchmark import case_bank
from backend.app.dataset.scenarios import build_manifest, InconsistentCase

specs = case_bank()
print(f"{len(specs)} specs")
fail = 0
for i, s in enumerate(specs):
    try:
        m = build_manifest(s)
    except InconsistentCase as e:
        fail += 1
        print(f"INCONSISTENT {s.get('template')} v{s.get('variant')}: {e}")
    except Exception as e:
        fail += 1
        print(f"ERROR {s.get('template')} v{s.get('variant')}: {type(e).__name__}: {e}")
        traceback.print_exc()
print("failures:", fail)
