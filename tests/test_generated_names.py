"""Every name a generated module reads is defined in it, and every module imports.

Each test generates the Python for a VI of the sample corpus together with its
dependencies, then checks the package the way a user would use it: the names its
modules read are bound, its relative imports find the modules they name, and the
modules import and run.
"""

from __future__ import annotations

import ast
import builtins
import importlib
import re
import sys
from pathlib import Path

import pytest

from lvkit.long_paths import extended_length
from lvkit.pipeline import generate_python
from tests.corpus_graphs import OPENG_USERLIB

pytestmark = pytest.mark.needs_samples

SAMPLES = Path(".lvkit/cache/samples")
OPENG = SAMPLES / "OpenG/extracted"
JKI = SAMPLES / "JKI-VI-Tester/source"
CALL_TEST_METHOD = JKI / "Classes/TestCase/private/CallTestMethod.vi"
TESTCASE_RUN = JKI / "Classes/TestCase/run.vi"
NAME_OR_FORMAT = JKI / "Utilities/Get Name Or Data Format As String.vi"
PATH_IS_TEST_CLASS = JKI / "Utilities/Check if Path Is Test Class.vi"
CLOSE_GENERIC_REFNUM = (
    OPENG / "File Group 0/user.lib/_OpenG.lib/appcontrol/appcontrol.llb"
    "/Close Generic Object Refnum__ogtk.vi"
)
DICTIONARY_HAS_KEY = (
    OPENG / "File Group 0/user.lib/_OpenG.lib/dictionary/dictionary.llb"
    "/Dictionary Has Key__ogtk.vi"
)
TDM_REGISTRY = (
    SAMPLES / "actor-framework/Core/ActorFramework/tracing/Support/TDM Registry.vi"
)
RECREATE_CLIENT = (
    SAMPLES / "measurement-plugin-labview/Source/Runtime/Clients/Discovery V1"
    "/Recreate Client.vi"
)
UNRESERVE_SESSIONS = (
    SAMPLES / "measurement-plugin-labview/Source/Runtime/Clients"
    "/Session Management V1/Session Reservation/Unreserve Sessions.vi"
)


def _generate(vi: Path, tmp_path: Path) -> Path:
    """The package generated for ``vi`` and everything it depends on."""
    if not vi.exists():
        pytest.skip(f"corpus VI missing: {vi}")
    result = generate_python(vi, tmp_path / "out", userlib_root=OPENG_USERLIB)
    # The package nests deeper than 260 characters; Windows with long paths off
    # opens it only in the extended-length form.
    return extended_length(result["output_dir"])


def _modules(package: Path) -> list[Path]:
    return sorted(p for p in package.rglob("*.py") if not p.name.endswith(".error.py"))


def _key(package: Path, module: Path) -> str:
    return module.relative_to(package).as_posix()


def _module_named(package: Path, suffix: str) -> Path:
    (module,) = (p for p in _modules(package) if p.as_posix().endswith(suffix))
    return module


def _module_defining(package: Path, class_name: str) -> Path:
    """The module that defines ``class_name``."""
    (module,) = (
        p
        for p in _modules(package)
        if re.search(rf"^class {class_name}[(:]", p.read_text(), re.MULTILINE)
    )
    return module


def _discovery_module(package: Path, stem: str) -> Path:
    """The module of the Discovery V1 client VI named ``stem``."""
    (module,) = (
        p
        for p in _modules(package)
        if p.parent.name == "discovery_v1" and p.stem.startswith(stem)
    )
    return module


def _undefined_names(source: str) -> set[str]:
    """Names the module reads that no assignment, import, def, class or parameter
    binds anywhere in it, and that are not builtins."""
    tree = ast.parse(source)
    bound = set(dir(builtins)) | {"__file__", "__package__"}  # module attributes
    loaded: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            (loaded if isinstance(node.ctx, ast.Load) else bound).add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.FunctionDef | ast.ClassDef):
            bound.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            bound.update((a.asname or a.name).split(".")[0] for a in node.names)
    return loaded - bound


def _all_undefined(package: Path) -> dict[str, set[str]]:
    found = {
        module.name: undefined
        for module in _modules(package)
        if (undefined := _undefined_names(module.read_text()))
    }
    return found


def _unresolved_relative_imports(package: Path) -> list[str]:
    """Relative imports whose module is not a file of the package; one whose
    module is a VI that failed to generate (an ``.error.py`` of that name) is not
    counted."""
    failed = {p.name.removesuffix(".error.py") for p in package.rglob("*.error.py")}
    missing: list[str] = []
    for module in _modules(package):
        for node in ast.walk(ast.parse(module.read_text())):
            if not isinstance(node, ast.ImportFrom) or not node.level:
                continue
            base = module.parent
            for _ in range(node.level - 1):
                base = base.parent
            parts = (node.module or "").split(".") if node.module else []
            target = base.joinpath(*parts)
            if parts and not target.is_dir():
                found = target.with_suffix(".py").exists() or target.name in failed
            else:
                found = all(
                    (target / f"{a.name}.py").exists() or a.name in failed
                    for a in node.names
                )
            if not found:
                missing.append(f"{module.name}: {ast.unparse(node)}")
    return missing


def _import_all(package: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Import every module of the package; return them by path in the package."""
    monkeypatch.syspath_prepend(str(package.parent))
    imported = {}
    for module in _modules(package):
        parts = module.relative_to(package.parent).with_suffix("").parts
        imported[module.relative_to(package).as_posix()] = importlib.import_module(
            ".".join(parts)
        )
    return imported


@pytest.fixture(autouse=True)
def _forget_generated_packages():
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        del sys.modules[name]


def test_annotation_types_are_imported_and_defined(tmp_path, monkeypatch):
    package = _generate(CALL_TEST_METHOD, tmp_path)
    assert _all_undefined(package) == {}
    assert _unresolved_relative_imports(package) == []

    # CallTestMethod is a method of the class module, annotated with the enum.
    call = _module_named(package, "classes/testcase/testcase.py").read_text()
    guarded = next(n for n in ast.parse(call).body if isinstance(n, ast.If))
    imported = {a.name for n in guarded.body for a in n.names}
    assert "MethodEnum" in imported
    assert "def __calltestmethod(self, method: MethodEnum" in call

    modules = _import_all(package, monkeypatch)
    enum = modules[
        _key(package, _module_named(package, "testcase/private/method__enumctl.py"))
    ]
    assert [(m.name, m.value) for m in enum.MethodEnum] == [
        ("setUp", 0),
        ("testMethod", 1),
        ("tearDown", 2),
    ]
    testcase = modules[_key(package, _module_defining(package, "TestCase"))]
    assert testcase.TestCase.__bases__ == (object,)


def test_enum_constant_is_imported_at_run_time(tmp_path):
    package = _generate(TESTCASE_RUN, tmp_path)
    assert _all_undefined(package) == {}
    # `run` is a method of the class module; the class is in an import cycle with
    # the modules it calls, so the enum is imported where `run` uses it.
    testcase = ast.parse(
        _module_named(package, "classes/testcase/testcase.py").read_text()
    )
    (run,) = (
        n
        for c in testcase.body
        if isinstance(c, ast.ClassDef) and c.name == "TestCase"
        for n in c.body
        if isinstance(n, ast.FunctionDef) and n.name == "run"
    )
    runtime = [
        a.name for n in ast.walk(run) if isinstance(n, ast.ImportFrom) for a in n.names
    ]
    assert "MethodEnum" in runtime
    assert "MethodEnum.setUp" in ast.unparse(run)


def test_error_cluster_tunnels_have_no_value(tmp_path, monkeypatch):
    package = _generate(NAME_OR_FORMAT, tmp_path)
    module = _module_named(package, "get_name_or_data_format_as_string.py")
    assert "error_out" not in module.read_text()
    assert _all_undefined(package) == {}
    assert list(package.rglob("*.error.py")) == []
    _import_all(package, monkeypatch)


def test_unbundling_an_error_cluster_takes_the_no_error_value(tmp_path):
    package = _generate(DICTIONARY_HAS_KEY, tmp_path)
    assert _all_undefined(package) == {}
    source = _module_named(package, "dictionary_has_key__ogtk.py").read_text()
    assert "selector" not in source


def test_a_function_that_returns_only_an_error_has_no_result_class(
    tmp_path, monkeypatch
):
    package = _generate(CLOSE_GENERIC_REFNUM, tmp_path)
    assert _all_undefined(package) == {}
    module = _module_named(package, "close_generic_object_refnum__ogtk.py")
    scalar = next(
        n
        for n in ast.parse(module.read_text()).body
        if isinstance(n, ast.FunctionDef)
        and n.name == "close_generic_object_refnum_scalar__ogtk"
    )
    assert scalar.returns is None
    _import_all(package, monkeypatch)


def test_a_disable_structure_generates_its_enabled_subdiagram(tmp_path):
    package = _generate(PATH_IS_TEST_CLASS, tmp_path)
    source = _module_named(package, "check_if_path_is_test_class.py").read_text()
    assert "Unknown node type" not in source
    assert "get_class_inheritance_from_class_path(" in source
    assert _all_undefined(package) == {}


def test_a_subvi_result_is_not_resolved_through_the_called_vi(tmp_path):
    package = _generate(RECREATE_CLIENT, tmp_path)
    source = _discovery_module(package, "recreate_client").read_text()
    assert "grpc_client_id_out = create_client_result.grpc_client_id" in source
    assert _all_undefined(package) == {}


def test_in_place_element_on_an_array_edits_the_array(tmp_path, monkeypatch):
    package = _generate(TDM_REGISTRY, tmp_path)
    assert list(package.rglob("*.error.py")) == []
    assert _all_undefined(package) == {}
    modules = _import_all(package, monkeypatch)
    registry = modules[_key(package, _module_named(package, "/tdm_registry.py"))]

    # Registering a notifier twice replaces its entry in place; each run ends in
    # the Type Cast of a refnum to a U32, which flattens as a 32-bit integer.
    for tdm_id, details in (("a", ("q", "c")), ("b", ("q2", "c2"))):
        registry.tdm_registry(
            action=0, tdm_id=tdm_id, delivery_notifier=7, details=details
        )
    (state,) = (v for k, v in vars(registry).items() if k.startswith("_lv_state_"))
    assert state == ([7], ["b"], [("q2", "c2")])


def test_in_place_element_borders_take_their_values_from_the_structure(tmp_path):
    package = _generate(UNRESERVE_SESSIONS, tmp_path)
    assert _all_undefined(package) == {}
    assert _unresolved_relative_imports(package) == []
    source = _module_named(package, "session_reservation/session_reservation.py")
    assert (
        "is_dvr_valid(dvr_valid_in=self.__session_and_driver_infos[0].dvr_valid)"
    ) in source.read_text()


WAVEGEN_LIB = SAMPLES / "lv-flex-channel-examples/WaveGen/WaveGen.lvlib"


def test_modules_mirror_the_project_folders(tmp_path):
    package = _generate(CALL_TEST_METHOD, tmp_path)
    paths = {m.relative_to(package).as_posix() for m in _modules(package)}
    assert any(p.endswith("classes/testcase/testcase.py") for p in paths)
    assert any(p.endswith("classes/testsuite/testsuite.py") for p in paths)
    assert not any(
        p.endswith("classes/testcase/private/calltestmethod.py") for p in paths
    )
    for path in paths:
        assert not {"home", "lvkit", "samples"} & set(path.split("/"))


def test_a_library_has_no_module_and_nothing_imports_one(tmp_path, monkeypatch):
    package = _generate(WAVEGEN_LIB, tmp_path)
    assert [m for m in _modules(package) if m.stem.endswith("lvlib")] == []
    assert _unresolved_relative_imports(package) == []
    _import_all(package, monkeypatch)
