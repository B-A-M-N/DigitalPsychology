"""Keep the shared runtime package independent of products and harnesses."""
from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "cognitive_runtime_core"
CORE_FILES = tuple(sorted(CORE.glob("*.py")))
INTEGRATION_FILES = tuple(sorted((CORE / "integrations").rglob("*.py")))
FORBIDDEN_IMPORT_ROOTS = (
    "digital_psychology", "codex_host", "codex_app_server_adapter",
    "cognitive_runtime", "runtime_contract", "runtime_host",
    "cognitiveframeworks", "cognitive_statework", "cognitivestatework",
    "digital_psychology_adapter", "digitalpsychology_adapter", "lib",
    "cohort", "operator_ui",
    "resolver", "resolve_runtime", "runtime", "codex_app_server_dp_bridge",
    "control_plane", "state_store", "transition_engine", "tuid",
    "workflow_authority", "workflow_controller", "workflow_engines",
)
FORBIDDEN_PROTOCOL_MARKERS = (
    "codex_host", "codex_app_server_adapter", "thread/start", "turn/start",
    "item/tool/call", "CODEX_HOME", "Codex", "Claude", "Athena",
    "DigitalPsychology", "CognitiveFrameWorks", "CognitiveStateWorks",
)
PRODUCT_ABBREVIATION = re.compile(r"\b(?:DP|CFW|CSW)\b", re.IGNORECASE)


def _import_targets(path: Path) -> list[tuple[int, str]]:
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


def test_boundary_scanner_finds_literal_dynamic_imports(tmp_path):
    source = tmp_path / "dynamic_import.py"
    source.write_text(
        "from importlib import import_module as load\n"
        "load('control_plane.workflow_authority')\n",
        encoding="utf-8")

    assert (2, "control_plane.workflow_authority") in _import_targets(source)


def test_neutral_package_has_no_product_or_harness_imports():
    violations: list[str] = []
    for path in CORE_FILES:
        for line, target in _import_targets(path):
            if any(target == root or target.startswith(root + ".")
                   for root in FORBIDDEN_IMPORT_ROOTS):
                violations.append(f"{path.relative_to(ROOT)}:{line}: {target}")
    assert violations == []


def test_neutral_package_has_no_harness_protocol_names():
    violations = []
    for path in CORE_FILES:
        source = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN_PROTOCOL_MARKERS:
            if marker.casefold() in source.casefold():
                violations.append(f"{path.relative_to(ROOT)}: {marker}")
    assert violations == []


def test_neutral_package_does_not_name_cognitive_applications():
    violations = []
    for path in CORE_FILES:
        source = path.read_text(encoding="utf-8")
        if PRODUCT_ABBREVIATION.search(source):
            violations.append(str(path.relative_to(ROOT)))
    assert violations == []


def test_neutral_distribution_declares_nonstandard_imports():
    metadata = (CORE / "pyproject.toml").read_text(encoding="utf-8")
    project = re.search(r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)", metadata)
    assert project is not None
    dependency_list = re.search(
        r"(?ms)^dependencies\s*=\s*(\[[^\]]*\])", project.group(1))
    assert dependency_list is not None
    declared = {
        re.split(r"[<>=!~;\[]", dependency, maxsplit=1)[0]
        .strip().replace("-", "_").split(".")[0]
        for dependency in ast.literal_eval(dependency_list.group(1))
    }

    package_files = CORE_FILES + tuple(
        path for path in INTEGRATION_FILES
        if path.relative_to(CORE).parts[1] != "codex")
    imported = {
        target.split(".")[0]
        for path in package_files for _line, target in _import_targets(path)
        if target.split(".")[0] not in sys.stdlib_module_names
        and target.split(".")[0] != "cognitive_runtime_core"
    }

    assert imported <= declared, {
        "undeclared_import_roots": sorted(imported - declared),
        "declared_dependencies": sorted(declared),
    }


def test_dp_distribution_depends_on_but_does_not_bundle_runtime_core():
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project = re.search(r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)", metadata)
    assert project is not None
    dependency_list = re.search(
        r"(?ms)^dependencies\s*=\s*(\[[^\]]*\])", project.group(1))
    assert dependency_list is not None
    dependencies = ast.literal_eval(dependency_list.group(1))
    assert "cognitive-runtime-core>=1.5,<2" in dependencies

    setuptools = re.search(
        r"(?ms)^\[tool\.setuptools\]\s*(.*?)(?=^\[|\Z)", metadata)
    assert setuptools is not None
    package_list = re.search(
        r"(?ms)^packages\s*=\s*(\[[^\]]*\])", setuptools.group(1))
    assert package_list is not None
    packages = ast.literal_eval(package_list.group(1))
    assert not any(package == "cognitive_runtime_core"
                   or package.startswith("cognitive_runtime_core.")
                   for package in packages)


def test_integration_adapters_import_no_cognitive_application():
    violations: list[str] = []
    for path in INTEGRATION_FILES:
        optional_cfw_host = path.relative_to(CORE).parts[:3] == (
            "integrations", "codex", "cfw_host")
        for line, target in _import_targets(path):
            forbidden = any(target == root or target.startswith(root + ".")
                            for root in FORBIDDEN_IMPORT_ROOTS)
            cfw_optional_import = (
                optional_cfw_host
                and target.split(".", 1)[0] in {
                    "runtime_contract", "runtime_host", "runtime_session",
                }
            )
            if forbidden and not cfw_optional_import:
                violations.append(f"{path.relative_to(ROOT)}:{line}: {target}")
    assert violations == []


def test_codex_adapter_is_separate_from_neutral_core_distribution():
    core_metadata = (CORE / "pyproject.toml").read_text(encoding="utf-8")
    adapter_metadata = (CORE / "integrations" / "codex" / "pyproject.toml")
    adapter_text = adapter_metadata.read_text(encoding="utf-8")

    assert '"cognitive_runtime_core.integrations.codex"' not in core_metadata
    assert 'name = "cognitive-runtime-codex-adapter"' in adapter_text
    assert 'cfw = ["cognitive-frameworks-runtime>=1.4,<2"]' in adapter_text
    core_version = re.search(
        r'^version = "(\d+)\.(\d+)\.(\d+)"$', core_metadata, re.MULTILINE)
    core_requirement = re.search(
        r'"cognitive-runtime-core>=(\d+)\.(\d+),<(\d+)"', adapter_text)
    assert core_version is not None
    assert core_requirement is not None
    installed_version = tuple(map(int, core_version.groups()))
    minimum_version = (int(core_requirement[1]), int(core_requirement[2]), 0)
    assert installed_version >= minimum_version
    assert installed_version[0] < int(core_requirement[3])


def test_codex_adapter_import_does_not_load_other_integrations():
    script = """
import sys
import cognitive_runtime_core
assert 'cognitive_runtime_core.integrations' not in sys.modules
import cognitive_runtime_core.integrations
assert not any(name.startswith('cognitive_runtime_core.integrations.')
               for name in sys.modules)
from cognitive_runtime_core.integrations.codex import CodexHarnessAdapter
assert CodexHarnessAdapter is not None
assert not any(name.startswith(
    'cognitive_runtime_core.integrations.codex.cfw_host')
    for name in sys.modules)
for product in ('athena', 'cfw', 'triad', 'reference'):
    assert not any(name == 'cognitive_runtime_core.integrations.' + product
                   or name.startswith('cognitive_runtime_core.integrations.' + product + '.')
                   for name in sys.modules), product
"""
    subprocess.run([sys.executable, "-c", script], cwd=ROOT, check=True,
                   capture_output=True, text=True)


def test_application_integration_convenience_exports_are_lazy():
    script = """
import sys
from cognitive_runtime_core.integrations import ControlActuator
assert ControlActuator is not None
assert 'cognitive_runtime_core.integrations.triad.actuator' in sys.modules
for product in ('athena', 'cfw', 'reference'):
    assert not any(name == 'cognitive_runtime_core.integrations.' + product
                   or name.startswith('cognitive_runtime_core.integrations.' + product + '.')
                   for name in sys.modules), product
"""
    subprocess.run([sys.executable, "-c", script], cwd=ROOT, check=True,
                   capture_output=True, text=True)


def test_legacy_runtime_paths_reexport_neutral_contracts():
    import cognitive_runtime_core
    from cognitive_runtime_core.integrations.athena import AthenaRuntimeAdapter
    from cognitive_runtime_core.integrations.reference import ReferenceHarnessAdapter
    from lib import runtime as legacy_runtime
    from lib.runtime.adapters import AthenaRuntimeAdapter as LegacyAthenaAdapter
    from lib.runtime.adapters import ReferenceHarnessAdapter as LegacyReferenceAdapter

    assert legacy_runtime.HarnessCapabilities is cognitive_runtime_core.HarnessCapabilities
    assert legacy_runtime.HostRuntimeBindings is cognitive_runtime_core.HostRuntimeBindings
    assert legacy_runtime.ValidatorReuseCache is cognitive_runtime_core.ValidatorReuseCache
    assert LegacyAthenaAdapter is AthenaRuntimeAdapter
    assert legacy_runtime.ReferenceHarnessAdapter is ReferenceHarnessAdapter
    assert LegacyReferenceAdapter is ReferenceHarnessAdapter
