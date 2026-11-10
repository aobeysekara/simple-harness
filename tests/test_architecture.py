import ast
import sys
from pathlib import Path


def test_core_imports_only_standard_library_and_core_modules() -> None:
    core = {
        "__init__",
        "_context",
        "domain",
        "ports",
        "harness",
        "admin",
        "auth",
        "validation",
        "runtime",
        "reuse",
    }
    for name in core:
        path = Path(__file__).parents[1] / "src" / "simple_framework" / f"{name}.py"
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                continue
            for module in modules:
                root = module.split(".")[0]
                assert root in sys.stdlib_module_names or module in {
                    f"simple_framework.{part}" for part in core
                }, (path, module)
