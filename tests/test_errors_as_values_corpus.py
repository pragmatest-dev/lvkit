"""The corpus VIs that exposed the defects of the exception mapping (the PR #234
tester round and the inventory of
``docs/_internal/design/errors-as-values-inventory.md``), generated with the VIs
they call and checked for the behaviour LabVIEW has.

The VIs call LabVIEW objects, so the modules are checked as source: each compiles,
and the shape the defect is about is read from its syntax tree. They fail until the
errors-as-values work lands (``xfail(strict=True)``), and each loses its mark in the
commit that implements its construct.
"""

from __future__ import annotations

import ast
import builtins
import re
from pathlib import Path

import pytest

from lvkit.load_mode import LoadMode
from lvkit.pipeline import generate_python

SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"

ENUMERATE_COMPUTE_NODES = (
    "measurement-plugin-labview/Source/Runtime/Clients/Discovery V1/"
    "Enumerate Compute Nodes.vi"
)
LV_ICON = "ni-labview-icon-editor/resource/plugins/lv_icon.vi"
PREPARE_LV = "actor-framework/Tooling/Prepare LV to Use Icon Editor Source.vi"
RESERVE_SESSIONS = (
    "measurement-plugin-labview/Source/Runtime/Clients/Session Management V1/"
    "Session Reservation/Reserve Sessions Wrapper.vi"
)
FRAMEWORK_TEST = "JKI-VI-Tester/source/Tests/Framework Test.vi"
GET_CONTROL_REF = "JKI-VI-Tester/source/Utilities/GetControlRef.vi"


def errors_as_values(construct: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(strict=True, reason=f"errors as values: {construct}")


def generated(
    tmp_path: Path, vi: str, mode: LoadMode = LoadMode.FULL
) -> tuple[str, ast.Module]:
    """The module generated for ``vi`` (with the VIs it calls unless ``mode`` is
    ``NONE``), compiled and parsed."""
    result = generate_python(SAMPLES / vi, tmp_path / "gen", mode=mode)
    stem = re.sub(r"\W+", "_", Path(vi).stem).strip("_").lower()
    path = next(Path(result["output_dir"]).rglob(f"{stem}.py"))
    source = path.read_text(encoding="utf-8")
    compile(source, str(path), "exec")
    return source, ast.parse(source)


def function(tree: ast.Module) -> ast.FunctionDef:
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    return functions[-1]


def parameters(tree: ast.Module) -> list[str]:
    args = function(tree).args
    return [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]


def result_fields(tree: ast.Module) -> list[str]:
    """The fields of the result class the module defines."""
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name.endswith("Result"):
            return [
                n.target.id
                for n in node.body
                if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
            ]
    return []


def calls(tree: ast.Module, name: str) -> int:
    return sum(
        1
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == name
    )


NO_HELD_ERROR_MODEL = ("_held_error", "ThreadPoolExecutor", "concurrent.futures")


@pytest.mark.needs_samples
def test_enumerate_compute_nodes_retries_on_the_code_its_error_case_names(
    tmp_path: Path,
) -> None:
    """The inner error case has frames No Error, Error -1012 and Default; the
    Default frame calls the service again after Recreate Client. Every frame of
    every error case is generated, and nothing raises a LabVIEW error."""
    source, tree = generated(tmp_path, ENUMERATE_COMPUTE_NODES)
    assert "error_in" in parameters(tree)
    assert "error_out" in result_fields(tree)
    assert "-1012" in source
    assert calls(tree, "discoveryservice_enumeratecomputenodes") == 2
    assert "except LabVIEWError" not in source


@pytest.mark.needs_samples
def test_lv_icon_has_no_held_error_model(tmp_path: Path) -> None:
    """lv_icon.vi runs its error chains in parallel and merges them. Branches are
    plain sequential code, each with its own error variable."""
    source, tree = generated(tmp_path, LV_ICON, LoadMode.NONE)
    for text in NO_HELD_ERROR_MODEL:
        assert text not in source
    assert "raise _held_error" not in source


@pytest.mark.needs_samples
def test_prepare_lv_handles_error_43_and_does_not_raise_it_again(
    tmp_path: Path,
) -> None:
    """Merge Errors feeds an error case with frames No Error, Error 43 (nothing to
    do) and Default (Simple Error Handler, which shows no dialog). An error runs
    neither frame's work and is not raised; the No Error frame's work runs only
    when no error came out of Merge Errors. Simple Error Handler is not the broken
    dictionary template. The VI has no error terminals, so it returns no result."""
    source, tree = generated(tmp_path, PREPARE_LV)
    for text in NO_HELD_ERROR_MODEL:
        assert text not in source
    assert "if not (merged_error is not None and merged_error.status):" in source
    assert "error_in.get" not in source
    assert "RuntimeError" not in source
    assert result_fields(tree) == []


@pytest.mark.needs_samples
def test_reserve_sessions_wrapper_runs_its_error_frame_and_keeps_the_error(
    tmp_path: Path,
) -> None:
    """The Default frame of the inner error case calls Session Client FGV and
    ReserveSessions again; its work is generated and the error is not raised
    again after it."""
    source, tree = generated(tmp_path, RESERVE_SESSIONS)
    assert "error_in" in parameters(tree)
    assert "error_out" in result_fields(tree)
    assert calls(tree, "session_client_fgv") >= 2
    assert "except LabVIEWError" not in source
    assert not re.search(r"^\s*raise\s*$", source, re.MULTILINE)


def _while_loops(tree: ast.Module) -> list[ast.While]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.While)]


def _breaks_only_under_a_condition(loop: ast.While) -> bool:
    """The loop's body does not end in a plain ``break`` and a ``break`` sits under an
    ``if``."""
    if isinstance(loop.body[-1], ast.Break):
        return False
    return any(
        isinstance(inner, ast.Break)
        for stmt in loop.body
        if isinstance(stmt, ast.If)
        for inner in ast.walk(stmt)
    )


@pytest.mark.needs_samples
def test_framework_test_loops_until_the_error(tmp_path: Path) -> None:
    """A While Loop whose stop terminal is wired to an error cluster loops until
    the error: the generated loop does not end in an unconditional ``break``."""
    _, tree = generated(tmp_path, FRAMEWORK_TEST, LoadMode.NONE)
    (loop,) = _while_loops(tree)
    assert _breaks_only_under_a_condition(loop)


@pytest.mark.needs_samples
def test_get_control_ref_stops_its_loop_on_the_status_of_the_error(
    tmp_path: Path,
) -> None:
    """The loop's stop is the status of the error its Property Node returns, joined
    with the other stop conditions: the status is read from the error, not the
    constant False, and the error is a parameter and a result field."""
    _, tree = generated(tmp_path, GET_CONTROL_REF)
    assert "error_in" in parameters(tree)
    assert "error_out" in result_fields(tree)
    (loop,) = _while_loops(tree)
    statuses = [
        n for n in ast.walk(loop) if isinstance(n, ast.Attribute) and n.attr == "status"
    ]
    assert statuses


ICON_EDITOR_FIRST_CALL = (
    "ni-labview-icon-editor/resource/plugins/NIIconEditor/Support/"
    "Icon Editor First Call.vi"
)


@pytest.mark.needs_samples
def test_error_code_read_from_a_conditional_is_one_operand(tmp_path: Path) -> None:
    """The error code of an Error Cluster From Error Code is an ``a if c else b``
    expression (an Unbundle of an error). It is parenthesised wherever the node
    puts it in a larger expression, so the module generates and compiles."""
    source, _ = generated(tmp_path, ICON_EDITOR_FIRST_CALL)
    assert "else 0) != 0 else" in source


DAQMX_MODULE_CONFIGURATION = (
    "DCAF-DAQModule/source/module/configuration/from string.vi"
)


@pytest.mark.needs_samples
def test_a_class_result_with_an_error_out_imports_the_error(tmp_path: Path) -> None:
    """The result classes in a class's module annotate ``error_out``, so the module
    imports ``LabVIEWError`` for its annotations."""
    result = generate_python(
        SAMPLES / DAQMX_MODULE_CONFIGURATION, tmp_path / "gen", mode=LoadMode.FULL
    )
    module = next(Path(result["output_dir"]).rglob("daqmx_module_configuration.py"))
    tree = ast.parse(module.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "LabVIEWError" in imported


def _undefined_annotation_names(tree: ast.Module) -> set[str]:
    """The names the annotations of a module use that it neither defines nor
    imports (an annotation is not evaluated, so the module still imports)."""
    defined = {name for name in dir(builtins)}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef | ast.FunctionDef):
            defined.add(node.name)
        elif isinstance(node, ast.ImportFrom | ast.Import):
            defined.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
    annotations = [
        node.annotation
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign | ast.arg) and node.annotation is not None
    ] + [
        node.returns
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.returns is not None
    ]
    return {
        name.id
        for annotation in annotations
        for name in ast.walk(annotation)
        if isinstance(name, ast.Name) and name.id not in defined
    }


@pytest.mark.needs_samples
def test_a_class_module_imports_every_type_its_annotations_name(
    tmp_path: Path,
) -> None:
    """Result classes and unwritten methods annotate the types of their
    terminals, so the module imports them."""
    result = generate_python(
        SAMPLES / DAQMX_MODULE_CONFIGURATION, tmp_path / "gen", mode=LoadMode.FULL
    )
    module = next(Path(result["output_dir"]).rglob("daqmx_module_configuration.py"))
    tree = ast.parse(module.read_text(encoding="utf-8"))
    assert _undefined_annotation_names(tree) == set()


IS_AN_ERROR_POLY = (
    "JKI-VI-Tester/source/JKI Reuse/"
    "Is an Error__jki_lib_error_handling -- VI Tester.vi"
)


@pytest.mark.needs_samples
def test_a_polymorphic_module_imports_each_name_once(tmp_path: Path) -> None:
    """The header of a polymorphic module and the imports its variants need do not
    repeat a name, and name no ``NamedTuple``."""
    result = generate_python(
        SAMPLES / IS_AN_ERROR_POLY, tmp_path / "gen", mode=LoadMode.FULL
    )
    header = "'The variants of the polymorphic VI "
    module = next(
        m
        for m in Path(result["output_dir"]).rglob("*.py")
        if m.read_text(encoding="utf-8").startswith(header)
    )
    source = module.read_text(encoding="utf-8")
    imports = [
        line for line in source.splitlines() if line.startswith(("import ", "from "))
    ]
    assert "NamedTuple" not in source
    assert len(imports) == len(set(imports))
    names = [
        alias.asname or alias.name
        for node in ast.parse(source).body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    ]
    assert len(names) == len(set(names))


BUILD_ERROR_CLUSTER = (
    "OpenG/extracted/File Group 0/user.lib/_OpenG.lib/error/error.llb/"
    "Build Error Cluster__ogtk.vi"
)


@pytest.mark.needs_samples
def test_an_error_imported_at_run_time_is_not_imported_again_for_annotations(
    tmp_path: Path,
) -> None:
    """A module that builds a ``LabVIEWError`` imports it once, not again under
    ``TYPE_CHECKING`` for its annotations."""
    source, _ = generated(tmp_path, BUILD_ERROR_CLUSTER)
    assert source.count("import LabVIEWError") == 1


READ_GENERAL_SETTINGS = (
    "JKI-VI-Tester/source/User Interfaces/Graphical Test Runner/"
    "Graphical Test Runner Support/Read General Settings.vi"
)


@pytest.mark.needs_samples
def test_a_catalog_function_returns_a_dataclass_result(tmp_path: Path) -> None:
    """Get System Directory's catalog function is written into the module with a
    result read by field name, like every other result."""
    result = generate_python(
        SAMPLES / READ_GENERAL_SETTINGS, tmp_path / "gen", mode=LoadMode.FULL
    )
    module = next(Path(result["output_dir"]).rglob("get_system_directory.py"))
    source = module.read_text(encoding="utf-8")
    assert "NamedTuple" not in source
    scope: dict[str, object] = {}
    exec(compile(source, str(module), "exec"), scope)  # noqa: S102
    found = scope["get_system_directory"](0)  # type: ignore[operator]
    assert found.system_directory_path == Path.home()


XML_LOOP_STACK = "JKI-EasyXML/Source/Fast Parser/XML Loop Stack Recursion.vi"


@pytest.mark.needs_samples
def test_an_inline_template_input_that_holds_braces_is_written_as_it_is(
    tmp_path: Path,
) -> None:
    """Toolkit Error Handling - Error Cluster From Error Code.vi feeds Trim
    Whitespace an f-string; the template's placeholders are read from the
    catalog template, so the f-string's own braces are no missing input."""
    result = generate_python(SAMPLES / XML_LOOP_STACK, tmp_path / "gen")
    assert not [g for g in result["gaps"] if "Trim Whitespace" in str(g)]
    module = next(
        Path(result["output_dir"]).rglob(
            "toolkit_error_handling___error_cluster_from_error_code.py"
        )
    )
    assert (
        "trim_whitespace_trimmed_string = f'<ERR>\\\\n{error_message}"
        in module.read_text(encoding="utf-8")
    )
