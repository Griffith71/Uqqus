"""Every `from ruqqus.helpers import name` names a module of ruqqus/helpers, and every `from ruqqus.helpers.x import y`
names something x defines. The test suite never imports the whole app (it needs Postgres, Redis and gevent), so a
broken import only shows when the site starts: this reads the source instead."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPERS = ROOT / "ruqqus" / "helpers"


def _sources():
    for folder in (ROOT / "ruqqus", ROOT / "scripts"):
        for path in folder.rglob("*.py"):
            if "node_modules" not in path.parts:
                yield path


def _defines(module_path):
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                parts = target.elts if isinstance(target, ast.Tuple) else [target]
                names.update(t.id for t in parts if isinstance(t, ast.Name))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
            if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
                return None                                  # a star import: anything may come from it
    return names


def test_every_helper_import_names_something_real():
    bad = []
    for path in _sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if node.module == "ruqqus.helpers":
                for alias in node.names:
                    if alias.name != "*" and not ((HELPERS / f"{alias.name}.py").exists() or (HELPERS / alias.name).is_dir()):
                        bad.append(f"{path.relative_to(ROOT)}: ruqqus.helpers has no module {alias.name}")
            elif node.module.startswith("ruqqus.helpers.") and node.module.count(".") == 2:
                module = HELPERS / f"{node.module.split('.')[-1]}.py"
                if not module.exists():
                    continue
                defined = _defines(module)
                if defined is None:
                    continue
                for alias in node.names:
                    if alias.name != "*" and alias.name not in defined:
                        bad.append(f"{path.relative_to(ROOT)}: {node.module} has no {alias.name}")
    assert bad == []
