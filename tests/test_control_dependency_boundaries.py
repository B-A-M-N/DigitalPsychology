"""Keep DP policy independent of application and harness implementations."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROL_ROOT = ROOT / "lib" / "control"
FORBIDDEN_IMPORT_ROOTS = frozenset({
    # CFW's installed runtime modules.
    "cognitive_runtime", "codex_app_server_adapter", "codex_host",
    "codex_app_server_dp_bridge", "digital_psychology_adapter", "cohort",
    "operator_ui", "resolve_runtime", "resolver", "runtime",
    "runtime_contract", "runtime_host", "runtime_session", "runtime_start",
    # CSW's implementation modules and installed package entry points.
    "cognitive_statework", "control_plane", "state_store",
    "transition_engine", "tuid", "workflow_authority", "workflow_controller",
    "workflow_engines",
})
FORBIDDEN_HARNESS_MARKERS = (
    "codex_host", "Codex", "thread/start", "turn/start", "item/tool/call",
    "CODEX_HOME", "Claude", "Athena",
)


def _literal_imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_names = {"import_module"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "importlib":
            imported_names.update(alias.asname or alias.name for alias in node.names
                                  if alias.name == "import_module")

    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.append((node.lineno, node.module))
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            dynamic = (
                isinstance(func, ast.Name) and func.id in imported_names
            ) or (isinstance(func, ast.Attribute) and func.attr == "import_module")
            dynamic = dynamic or (isinstance(func, ast.Name) and func.id == "__import__")
            target = node.args[0]
            if (dynamic and isinstance(target, ast.Constant)
                    and isinstance(target.value, str)):
                imports.append((node.lineno, target.value))
    return imports


def test_dp_control_does_not_import_application_implementations():
    violations = []
    for path in sorted(CONTROL_ROOT.rglob("*.py")):
        for line, module in _literal_imports(path):
            if any(module == root or module.startswith(root + ".")
                   for root in FORBIDDEN_IMPORT_ROOTS):
                violations.append(f"{path.relative_to(ROOT)}:{line}: {module}")
    assert not violations, (
        "DP control imports CFW/CSW or harness implementation modules:\n"
        + "\n".join(violations))


def test_dp_control_contains_no_harness_protocol_details():
    violations = []
    for path in sorted(CONTROL_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8").casefold()
        for marker in FORBIDDEN_HARNESS_MARKERS:
            if marker.casefold() in source:
                violations.append(f"{path.relative_to(ROOT)}: {marker}")
    assert not violations, (
        "Harness protocol details leaked into DP control:\n" + "\n".join(violations))
