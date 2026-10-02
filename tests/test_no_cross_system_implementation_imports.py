"""Guard the ownership boundary between CFW, CSW, and DP implementations.

The host may compose the systems through their public adapters and provider
contracts. None of the three implementation trees may import another
system's implementation package directly.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest


DP_ROOT = Path(__file__).resolve().parents[1]
PROJECTS = {
    "CFW": (
        Path(os.environ.get("CFW_SOURCE_ROOT", DP_ROOT.parent / "CognitiveFrameWorks")),
        "scripts",
        ("cognitive_statework", "digital_psychology", "digitalpsychology"),
    ),
    "CSW": (
        Path(os.environ.get("CSW_SOURCE_ROOT", DP_ROOT.parent / "CognitiveStateWork")),
        "scripts",
        ("cognitive_frameworks", "cognitiveframeworks", "cognitive_runtime",
         "resolve_runtime", "digital_psychology", "digitalpsychology"),
    ),
    "DP": (
        DP_ROOT,
        "lib",
        ("cognitive_statework", "cognitive_frameworks", "cognitiveframeworks",
         "cognitive_runtime", "resolve_runtime", "control_plane",
         "workflow_engines"),
    ),
}


def _implementation_imports(source_root: Path) -> tuple[tuple[Path, str], ...]:
    findings = []
    excluded = {".git", ".venv", "venv", "build", "dist", "__pycache__", "tests"}
    for path in source_root.rglob("*.py"):
        if excluded.intersection(path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.Import):
                findings.extend((path, alias.name) for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                module = node.module
            elif isinstance(node, ast.Call):
                function = node.func
                is_import_call = (
                    isinstance(function, ast.Name)
                    and function.id in {"__import__", "import_module"}
                ) or (
                    isinstance(function, ast.Attribute)
                    and function.attr == "import_module"
                )
                if (is_import_call and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)):
                    module = node.args[0].value
            if module:
                findings.append((path, module))
    return tuple(findings)


def test_no_cross_system_implementation_imports() -> None:
    missing = [name for name, (root, source, _forbidden) in PROJECTS.items()
               if not (root / source).is_dir()]
    if missing:
        pytest.skip("sibling source checkouts unavailable: " + ", ".join(missing))

    violations = []
    for name, (root, source, forbidden) in PROJECTS.items():
        for path, module in _implementation_imports(root / source):
            if any(module == prefix or module.startswith(prefix + ".")
                   for prefix in forbidden):
                violations.append(f"{name}: {path.relative_to(root)} imports {module}")

    assert not violations, "cross-system implementation imports found:\n" + "\n".join(
        sorted(violations))
