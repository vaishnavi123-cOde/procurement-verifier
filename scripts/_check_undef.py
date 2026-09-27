import re
import ast
import builtins as bi

targets = ["scenarios", "benchmark", "documents", "suppliers", "manifests"]
for f in targets:
    src = open(f"backend/app/dataset/{f}.py", encoding="utf-8").read()
    tree = ast.parse(src)
    assigned = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            assigned.add(node.name)
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    assigned.add(t.id)
        if isinstance(node, ast.Import):
            for a in node.names:
                assigned.add((a.asname or a.name).split(".")[0])
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                assigned.add(a.asname or a.name)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assigned.add(node.target.id)
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    reserved = {"and","or","not","is","None","True","False"}
    bset = set(dir(bi))
    undef = sorted(n for n in (used - assigned - reserved - bset) if not n.startswith("_"))
    print(f"{f}: undefined -> {undef}")
