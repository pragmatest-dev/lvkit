"""The spec of ``docs/_internal/design/errors-as-values.md`` as tests: one tiny
handmade VI per construct of the plan's table (``error_construct_diagrams.py``).

Every construct has two kinds of test:

* its views (describe, lvnet terse and verbose, JSON) print exactly the text
  pinned in ``error_views_expected.py``; they already show errors faithfully and
  do not change in this work;
* its generated Python is executed. An error is a value (``None`` or a
  ``LabVIEWError``), a VI's results are one frozen result object read by name
  with ``error_out`` as a field, and a LabVIEW error never raises out of the VI
  function. These fail until the work lands: each is marked
  ``xfail(strict=True)`` and loses its mark in the commit that implements its
  construct.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import tests.error_construct_diagrams as C
from tests.error_diagrams import (
    REQUIRED,
    Package,
    Project,
    failing_step,
    json_body,
    ok_step,
    project,  # noqa: F401  (the fixture)
    skipping_step,
)
from tests.error_views_expected import EXPECTED

Builder = Callable[[Project], str]

VIEW_CASES: dict[str, Builder] = {
    "vi_error_terminals": C.vi_error_terminals,
    "vi_error_in_required": lambda p: C.vi_error_terminals(p, REQUIRED),
    "subvi_call": C.subvi_call,
    "subvi_chain": C.subvi_chain,
    "unwired_error_in": C.unwired_error_in,
    "forked_error": C.forked_error,
    "catalog_node_skips": C.catalog_node_skips,
    "catalog_node_runs_despite_error": C.catalog_node_runs_despite_error,
    "native_failure": C.native_failure,
    "error_case_panel_fed": C.error_case_panel_fed,
    "error_case_codes": C.error_case_codes,
    "unbundle_error": C.unbundle_error,
    "status_selects_case": C.status_selects_case,
    "bundle_error": C.bundle_error,
    "merge_errors": C.merge_errors_in_order,
    "clear_errors": C.clear_errors,
    "clear_errors_code": lambda p: C.clear_errors(p, code=7),
    "error_cluster_from_error_code": C.error_cluster_from_error_code,
    "error_cluster_from_warning_code": lambda p: C.error_cluster_from_error_code(
        p, warning=True
    ),
    "simple_error_handler": C.simple_error_handler,
    "error_shift_register": C.error_shift_register,
    "error_loop_tunnel": C.error_loop_tunnel,
    "loop_stops_on_error": C.loop_stops_on_error,
    "error_constant_none": lambda p: C.error_constant(p, "none"),
    "error_constant_error": lambda p: C.error_constant(p, "error5"),
    "error_constant_warning": lambda p: C.error_constant(p, "warning5"),
    "error_local_variable": C.error_local_variable,
}


def errors_as_values(construct: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(strict=True, reason=f"errors as values: {construct}")


# =============================================================
# Views: the text every view prints today
# =============================================================


@pytest.mark.parametrize("case", sorted(VIEW_CASES))
def test_the_views_print_the_error_wires_faithfully(
    project: Project,  # noqa: F811
    case: str,
) -> None:
    name = VIEW_CASES[case](project)
    views = project.views(name)
    expected = EXPECTED[case]
    assert views.lvnet == expected["lvnet"]
    assert views.terse == expected["terse"]
    assert views.describe == expected["describe"]
    assert json_body(views) == expected["json"]


def test_every_construct_has_pinned_views() -> None:
    assert sorted(EXPECTED) == sorted(VIEW_CASES)


# =============================================================
# Generated Python: helpers
# =============================================================

# A subVI whose error out is the module global ``ERROR`` (a test sets it), that
# records every call.
CONFIGURABLE_STEP = """
    from types import SimpleNamespace

    ERROR = None
    CALLS = []

    def {fn}(x=0, error_in=None):
        CALLS.append({{"x": x, "error_in": error_in}})
        return SimpleNamespace(r=x + {add}, error_out=ERROR)
"""

# A subVI that fails the call number ``FAIL_ON`` (1-based) and skips on an incoming
# error, recording every call.
COUNTING_STEP = """
    from types import SimpleNamespace
    from .errors import LabVIEWError

    CALLS = []
    FAIL_ON = 2

    def {fn}(x=0, error_in=None):
        CALLS.append(error_in)
        assert len(CALLS) < 50, "runaway loop"
        if error_in is not None:
            return SimpleNamespace(r=0, error_out=error_in)
        if len(CALLS) == FAIL_ON:
            return SimpleNamespace(
                r=0, error_out=LabVIEWError(code=7, source="{fn}", status=True)
            )
        return SimpleNamespace(r=x, error_out=None)
"""

RECORDING_STEP = """
    from types import SimpleNamespace

    CALLS = []

    def {fn}(x=0, error_in=None):
        CALLS.append((x, error_in))
        return SimpleNamespace(r=x + {add}, error_out=error_in)
"""


def configurable_step(fn: str, add: int = 1) -> str:
    return CONFIGURABLE_STEP.format(fn=fn, add=add)


def counting_step(fn: str) -> str:
    return COUNTING_STEP.format(fn=fn)


def recording_step(fn: str, add: int = 1) -> str:
    return RECORDING_STEP.format(fn=fn, add=add)


def generate(
    project: Project,  # noqa: F811
    builder: Builder,
    stubs: dict[str, str] | None = None,
) -> Package:
    return project.generate(builder(project), stubs)


def assert_result_object(result: Any, *fields: str) -> None:
    """A VI's results are one frozen dataclass read by field name."""
    assert dataclasses.is_dataclass(result) and not isinstance(result, type)
    assert not isinstance(result, tuple)
    assert [f.name for f in dataclasses.fields(result)] == list(fields)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(result, fields[0], None)


# =============================================================
# VI with error in / error out on its connector pane
# =============================================================


def test_a_vi_takes_error_in_and_returns_error_out(project: Project) -> None:  # noqa: F811
    package = generate(project, C.vi_error_terminals)
    parameters = inspect.signature(package.function).parameters
    assert parameters["error_in"].default is None
    assert "error_in: LabVIEWError | None=None" in package.source
    result = package.function(x=1)
    assert_result_object(result, "result", "error_out")
    assert (result.result, result.error_out) == (2, None)


def test_error_in_flows_to_error_out_and_a_plain_node_still_runs(
    project: Project,  # noqa: F811
) -> None:
    package = generate(project, C.vi_error_terminals)
    error = package.error(7)
    result = package.function(x=1, error_in=error)
    assert result.error_out is error
    assert result.result == 2


def test_a_required_error_in_has_no_default(project: Project) -> None:  # noqa: F811
    package = generate(project, lambda p: C.vi_error_terminals(p, REQUIRED))
    parameter = inspect.signature(package.function).parameters["error_in"]
    assert parameter.default is inspect.Parameter.empty
    assert package.function(1, None).error_out is None


# =============================================================
# SubVI / method call
# =============================================================


def test_a_subvi_is_called_with_error_in_and_its_error_out_is_read(
    project: Project,  # noqa: F811
) -> None:
    package = generate(project, C.subvi_call, {"step_a": recording_step("step_a")})
    step = package.stub("step_a")
    result = package.function(x=1)
    assert_result_object(result, "result", "error_out")
    assert (result.result, result.error_out) == (2, None)
    assert step.CALLS == [(1, None)]
    error = package.error(7)
    result = package.function(x=1, error_in=error)
    assert step.CALLS == [(1, None), (1, error)]
    assert result.error_out is error


def test_a_subvi_is_always_called_and_decides_whether_to_skip(
    project: Project,  # noqa: F811
) -> None:
    package = generate(project, C.subvi_call, {"step_a": skipping_step("step_a")})
    step = package.stub("step_a")
    error = package.error(7)
    result = package.function(x=1, error_in=error)
    assert step.CALLS == []
    assert (result.result, result.error_out) == (0, error)
    assert package.function(x=1).result == 2
    assert step.CALLS == [1]


def test_a_node_downstream_of_a_failure_is_skipped(project: Project) -> None:  # noqa: F811
    stubs = {
        "step_a": failing_step("step_a", 7),
        "step_b": skipping_step("step_b", 10),
    }
    package = generate(project, C.subvi_chain, stubs)
    result = package.function(x=1)
    assert package.stub("step_b").CALLS == []
    assert (result.result, result.error_out.code) == (0, 7)
    assert result.error_out.status is True


def test_a_chain_without_a_failure_runs_every_node(project: Project) -> None:  # noqa: F811
    stubs = {"step_a": ok_step("step_a"), "step_b": skipping_step("step_b", 10)}
    package = generate(project, C.subvi_chain, stubs)
    result = package.function(x=1)
    assert package.stub("step_b").CALLS == [2]
    assert (result.result, result.error_out) == (12, None)


# =============================================================
# Catalog node with error in
# =============================================================


def test_a_catalog_node_runs_inside_a_guard_on_its_error_in(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = generate(project, C.catalog_node_skips)
    assert "if error_in is None or not error_in.status:" in package.source
    data = tmp_path / "data.bin"
    data.write_bytes(b"12345")
    with data.open("rb") as handle:
        result = package.function(file=handle)
    assert_result_object(result, "size", "error_out")
    assert (result.size, result.error_out) == (5, None)


def test_a_skipped_catalog_node_takes_its_type_default(
    project: Project,  # noqa: F811
) -> None:
    package = generate(project, C.catalog_node_skips)
    error = package.error(7)
    # The node would fail on this object; skipped, it does not run.
    result = package.function(file=object(), error_in=error)
    assert (result.size, result.error_out) == (0, error)


@errors_as_values("catalog node that runs despite an error")
def test_a_catalog_node_that_runs_despite_an_error_is_not_guarded(
    project: Project,  # noqa: F811
) -> None:
    package = generate(project, C.catalog_node_runs_despite_error)
    assert "if error_in is None or not error_in.status:" not in package.source
    error = package.error(7)
    result = package.function(file=SimpleNamespace(name="a.txt"), error_in=error)
    assert (str(result.path), result.error_out) == ("a.txt", error)


# =============================================================
# Native operation that can fail
# =============================================================


@errors_as_values("native operation that can fail")
def test_a_native_failure_becomes_the_error_out(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = generate(project, C.native_failure)
    assert "if error_in is None or not error_in.status:" in package.source
    assert "except" in package.source and "LabVIEWError(" in package.source
    result = package.function(path=tmp_path / "missing.bin")
    assert_result_object(result, "refnum", "error_out")
    assert result.refnum is None
    assert result.error_out.status is True
    assert result.error_out.code != 0
    assert result.error_out.message


def test_a_native_operation_that_works_leaves_the_error_out_empty(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = generate(project, C.native_failure)
    existing = tmp_path / "there.bin"
    existing.write_bytes(b"x")
    result = package.function(path=existing)
    assert result.error_out is None
    result.refnum.close()


def test_a_native_operation_does_not_run_after_an_error(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = generate(project, C.native_failure)
    existing = tmp_path / "there.bin"
    existing.write_bytes(b"x")
    error = package.error(7)
    result = package.function(path=existing, error_in=error)
    assert (result.refnum, result.error_out) == (None, error)


# =============================================================
# Error case structure
# =============================================================


def test_an_error_case_fed_from_error_in_runs_its_error_frame(
    project: Project,  # noqa: F811
) -> None:
    package = generate(project, C.error_case_panel_fed)
    assert "error_in is not None and error_in.status" in package.source
    ok = package.function(x=1)
    assert (ok.result, ok.error_out) == (2, None)
    error = package.error(7)
    failed = package.function(x=1, error_in=error)
    assert (failed.result, failed.error_out) == (100, error)


def test_a_warning_selects_the_no_error_frame(project: Project) -> None:  # noqa: F811
    package = generate(project, C.error_case_panel_fed)
    warning = package.error(5, status=False)
    result = package.function(x=1, error_in=warning)
    assert result.result == 2
    assert result.error_out is warning


@pytest.mark.parametrize(
    ("error", "expected"), [(None, 1), (7, 100), (9, 200), ("warning", 1)]
)
def test_an_error_case_picks_the_frame_of_the_error_code(
    project: Project,  # noqa: F811
    error: int | str | None,
    expected: int,
) -> None:
    package = generate(
        project, C.error_case_codes, {"step_a": configurable_step("step_a")}
    )
    if isinstance(error, int):
        package.stub("step_a").ERROR = package.error(error)
    elif error == "warning":
        package.stub("step_a").ERROR = package.error(5, status=False)
    result = package.function(x=1)
    assert result.result == expected
    assert result.error_out is package.stub("step_a").ERROR
    assert "== 7" in package.source


# =============================================================
# Unbundle / Bundle of an error
# =============================================================


@pytest.mark.parametrize(
    ("status", "code"),
    [(None, 0), (True, 7), (False, 5)],
    ids=["none", "error", "warning"],
)
def test_unbundle_reads_the_fields_of_the_error_on_its_wire(
    project: Project,  # noqa: F811
    status: bool | None,
    code: int,
) -> None:
    package = generate(project, C.unbundle_error)
    assert "error_in is not None and error_in.status" in package.source
    assert "error_in.code if error_in" in package.source
    error = None if status is None else package.error(code, status=status)
    result = package.function(error_in=error)
    assert_result_object(result, "failed", "code")
    assert (result.failed, result.code) == (bool(status), code)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (None, 1),
        (7, 100),
        ("warning", 1),
    ],
)
def test_the_status_of_an_error_selects_a_boolean_case(
    project: Project,  # noqa: F811
    error: int | str | None,
    expected: int,
) -> None:
    package = generate(project, C.status_selects_case)
    if error == "warning":
        value = package.error(5, status=False)
    else:
        value = None if error is None else package.error(error)
    assert package.function(error_in=value).result == expected


def test_bundle_builds_an_error_value(project: Project) -> None:  # noqa: F811
    package = generate(project, C.bundle_error)
    result = package.function(status=True, code=5, source="here")
    assert_result_object(result, "error_out")
    error = result.error_out
    assert isinstance(error, package.errors.LabVIEWError)
    assert (error.status, error.code, error.source) == (True, 5, "here")


def test_bundle_with_status_false_and_a_code_builds_a_warning(
    project: Project,  # noqa: F811
) -> None:
    package = generate(project, C.bundle_error)
    warning = package.function(status=False, code=5, source="here").error_out
    assert (warning.status, warning.code) == (False, 5)


def test_bundle_with_status_false_and_code_zero_is_no_error(
    project: Project,  # noqa: F811
) -> None:
    package = generate(project, C.bundle_error)
    assert package.function(status=False, code=0, source="").error_out is None


# =============================================================
# Merge Errors
# =============================================================


@pytest.mark.parametrize(
    ("a", "b", "merged_code", "merged_status"),
    [
        (7, 9, 7, True),
        (None, 9, 9, True),
        ("warning", 9, 9, True),
        ("warning", None, 5, False),
        (None, None, None, None),
    ],
    ids=["first-of-two", "second-only", "error-beats-warning", "warning", "none"],
)
def test_merge_errors_picks_the_first_error_in_input_order(
    project: Project,  # noqa: F811
    a: int | str | None,
    b: int | str | None,
    merged_code: int | None,
    merged_status: bool | None,
) -> None:
    stubs = {
        "step_a": configurable_step("step_a"),
        "step_b": configurable_step("step_b"),
        "step_c": skipping_step("step_c", 100),
    }
    package = generate(project, C.merge_errors_in_order, stubs)

    def value(spec: int | str | None) -> Any:
        if spec == "warning":
            return package.error(5, status=False)
        return None if spec is None else package.error(spec)

    package.stub("step_a").ERROR = value(a)
    package.stub("step_b").ERROR = value(b)
    result = package.function(x=1)
    error = result.error_out
    if merged_code is None:
        assert error is None
    else:
        assert (error.code, error.status) == (merged_code, merged_status)


def test_a_node_after_merge_errors_is_skipped_on_the_merged_error(
    project: Project,  # noqa: F811
) -> None:
    stubs = {
        "step_a": configurable_step("step_a"),
        "step_b": configurable_step("step_b"),
        "step_c": skipping_step("step_c", 100),
    }
    package = generate(project, C.merge_errors_in_order, stubs)
    package.stub("step_b").ERROR = package.error(9)
    result = package.function(x=1)
    assert package.stub("step_c").CALLS == []
    assert result.error_out.code == 9
    # Both branches ran: B's error did not stop A.
    assert len(package.stub("step_a").CALLS) == 1
    assert len(package.stub("step_b").CALLS) == 1


def test_a_node_after_merge_errors_runs_when_nothing_failed(
    project: Project,  # noqa: F811
) -> None:
    stubs = {
        "step_a": configurable_step("step_a"),
        "step_b": configurable_step("step_b"),
        "step_c": skipping_step("step_c", 100),
    }
    package = generate(project, C.merge_errors_in_order, stubs)
    assert package.function(x=1).result == 101
    assert "ThreadPoolExecutor" not in package.source


# =============================================================
# Clear Errors
# =============================================================


def test_clear_errors_lets_the_nodes_after_it_run(project: Project) -> None:  # noqa: F811
    stubs = {
        "step_a": failing_step("step_a", 7),
        "step_b": skipping_step("step_b", 10),
    }
    package = generate(project, C.clear_errors, stubs)
    result = package.function(x=1)
    assert package.stub("step_b").CALLS == [1]
    assert (result.result, result.error_out) == (11, None)


@pytest.mark.parametrize(
    ("code", "cleared"), [(7, True), (9, False)], ids=["the code", "another code"]
)
def test_clear_errors_with_a_specific_code_clears_only_that_code(
    project: Project,  # noqa: F811
    code: int,
    cleared: bool,
) -> None:
    stubs = {
        "step_a": failing_step("step_a", code),
        "step_b": skipping_step("step_b", 10),
    }
    package = generate(project, lambda p: C.clear_errors(p, code=7), stubs)
    result = package.function(x=1)
    if cleared:
        assert package.stub("step_b").CALLS == [1]
        assert result.error_out is None
    else:
        assert package.stub("step_b").CALLS == []
        assert result.error_out.code == 9


def test_clear_errors_leaves_a_clean_wire_alone(project: Project) -> None:  # noqa: F811
    stubs = {"step_a": ok_step("step_a"), "step_b": ok_step("step_b", 10)}
    package = generate(project, C.clear_errors, stubs)
    assert package.function(x=1).error_out is None


# =============================================================
# Error Cluster From Error Code
# =============================================================


@pytest.mark.parametrize(
    ("code", "expected"), [(0, None), (5, (True, 5))], ids=["zero", "code"]
)
def test_error_cluster_from_error_code_builds_the_error(
    project: Project,  # noqa: F811
    code: int,
    expected: tuple[bool, int] | None,
) -> None:
    package = generate(project, C.error_cluster_from_error_code)
    result = package.function(code=code)
    assert_result_object(result, "error_out")
    if expected is None:
        assert result.error_out is None
    else:
        error = result.error_out
        assert (error.status, error.code) == expected


def test_error_cluster_from_error_code_is_a_warning_when_asked(
    project: Project,  # noqa: F811
) -> None:
    package = generate(
        project, lambda p: C.error_cluster_from_error_code(p, warning=True)
    )
    error = package.function(code=5).error_out
    assert (error.status, error.code) == (False, 5)
    assert package.function(code=0).error_out is None


# =============================================================
# Simple Error Handler
# =============================================================


def test_simple_error_handler_reports_the_error_without_a_dialog(
    project: Project,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    package = generate(project, C.simple_error_handler)
    capsys.readouterr()  # what generating printed
    clean = package.function()
    assert_result_object(clean, "failed", "code", "error_out")
    assert (clean.failed, clean.code, clean.error_out) == (False, 0, None)
    error = package.error(7)
    result = package.function(error_in=error)
    assert (result.failed, result.code, result.error_out) == (True, 7, error)
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
    assert "RuntimeError" not in package.source


def test_simple_error_handler_reports_its_error_code_when_no_error_came_in(
    project: Project,  # noqa: F811
) -> None:
    """NI: when error in has no error, the VI tests error code; a nonzero code is
    an error, from error source. An error in wins over the code; a warning on
    error in with code 0 flows on."""
    package = generate(project, lambda p: C.simple_error_handler(p, with_code=True))
    fields = ("failed", "code", "source", "error_out")
    found = package.function(error_code=7, error_source="my step")
    assert_result_object(found, *fields)
    error = found.error_out
    assert (error.status, error.code, error.source) == (True, 7, "my step")
    assert (found.failed, found.code, found.source) == (True, 7, "my step")

    incoming = package.error(9)
    result = package.function(error_in=incoming, error_code=7, error_source="mine")
    assert (result.failed, result.code, result.error_out) == (True, 9, incoming)

    clean = package.function()
    assert (clean.failed, clean.code, clean.source, clean.error_out) == (
        False,
        0,
        "",
        None,
    )
    warning = package.error(5, status=False)
    result = package.function(error_in=warning)
    assert (result.failed, result.code, result.error_out) == (False, 5, warning)


def test_simple_error_handler_without_an_error_code_writes_no_code_test(
    project: Project,  # noqa: F811
) -> None:
    package = generate(project, C.simple_error_handler)
    assert "!= 0" not in package.source
    assert "LabVIEWError(" not in package.source


# =============================================================
# Loops
# =============================================================


def test_an_error_shift_register_keeps_the_loop_going_and_skips_later_calls(
    project: Project,  # noqa: F811
) -> None:
    package = generate(
        project, C.error_shift_register, {"step_a": counting_step("step_a")}
    )
    step = package.stub("step_a")
    result = package.function(x=5)
    assert_result_object(result, "result", "error_out")
    assert len(step.CALLS) == 3
    assert step.CALLS[:2] == [None, None]
    assert step.CALLS[2].code == 7
    assert (result.result, result.error_out.code) == (5, 7)


def test_an_error_shift_register_starts_from_the_error_in(
    project: Project,  # noqa: F811
) -> None:
    package = generate(
        project, C.error_shift_register, {"step_a": counting_step("step_a")}
    )
    error = package.error(9)
    result = package.function(x=5, error_in=error)
    assert package.stub("step_a").CALLS == [error, error, error]
    assert result.error_out is error


def test_an_error_goes_through_a_loop_by_tunnels(project: Project) -> None:  # noqa: F811
    package = generate(
        project, C.error_loop_tunnel, {"step_a": recording_step("step_a")}
    )
    step = package.stub("step_a")
    assert package.function(x=3).error_out is None
    error = package.error(7)
    result = package.function(x=3, error_in=error)
    assert step.CALLS == [(1, None), (1, error)]
    assert result.error_out is error


def test_a_while_loop_stops_when_its_stop_terminal_gets_an_error(
    project: Project,  # noqa: F811
) -> None:
    package = generate(
        project, C.loop_stops_on_error, {"step_a": counting_step("step_a")}
    )
    package.stub("step_a").FAIL_ON = 3
    result = package.function(x=5)
    assert len(package.stub("step_a").CALLS) == 3
    assert result.error_out.code == 7
    error = "step_a_result.error_out"
    assert f"if {error} is not None and {error}.status:\n" in package.source


# =============================================================
# Forked error wire, unwired error in
# =============================================================


@pytest.mark.parametrize("failing", ["a", "b"])
def test_every_branch_of_a_forked_error_wire_runs(
    project: Project,  # noqa: F811
    failing: str,
) -> None:
    stubs = {
        "step_a": configurable_step("step_a", 1),
        "step_b": configurable_step("step_b", 10),
    }
    package = generate(project, C.forked_error, stubs)
    package.stub(f"step_{failing}").ERROR = package.error(7)
    result = package.function(x=1)
    assert_result_object(result, "result_a", "result_b", "error_out_a", "error_out_b")
    assert (result.result_a, result.result_b) == (2, 11)
    assert (result.error_out_a is None) == (failing != "a")
    assert (result.error_out_b is None) == (failing != "b")
    assert "concurrent" not in package.source


def test_both_branches_of_a_forked_error_wire_receive_the_error_in(
    project: Project,  # noqa: F811
) -> None:
    stubs = {"step_a": recording_step("step_a"), "step_b": recording_step("step_b")}
    package = generate(project, C.forked_error, stubs)
    error = package.error(7)
    package.function(x=1, error_in=error)
    assert package.stub("step_a").CALLS == [(1, error)]
    assert package.stub("step_b").CALLS == [(1, error)]


def test_a_node_with_an_unwired_error_in_runs_after_a_failure(
    project: Project,  # noqa: F811
) -> None:
    stubs = {
        "step_a": failing_step("step_a", 7),
        "step_b": skipping_step("step_b", 10),
    }
    package = generate(project, C.unwired_error_in, stubs)
    result = package.function(x=1)
    assert package.stub("step_b").CALLS == [0]
    assert (result.result, result.error_out) == (10, None)


# =============================================================
# Error constant, error local variable
# =============================================================


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("none", None), ("error5", (True, 5)), ("warning5", (False, 5))],
)
def test_an_error_constant_is_its_value(
    project: Project,  # noqa: F811
    kind: str,
    expected: tuple[bool, int] | None,
) -> None:
    package = generate(project, lambda p: C.error_constant(p, kind))
    error = package.function().error_out
    if expected is None:
        assert error is None
    else:
        assert (error.status, error.code) == expected


def test_an_error_local_variable_is_a_function_variable(
    project: Project,  # noqa: F811
) -> None:
    package = generate(
        project, C.error_local_variable, {"step_a": skipping_step("step_a")}
    )
    step = package.stub("step_a")
    result = package.function(x=1)
    assert_result_object(result, "result", "error_out")
    assert (result.result, result.error_out, step.CALLS) == (2, None, [1])
    error = package.error(7)
    result = package.function(x=1, error_in=error)
    assert (result.error_out, step.CALLS) == (error, [1])


# =============================================================
# What does not change
# =============================================================


def test_a_gap_still_raises_not_implemented_at_run_time(
    project: Project,  # noqa: F811
) -> None:
    """An unknown primitive is a gap: generation warns and the node raises
    ``NotImplementedError``, whatever the error in."""
    from tests.error_diagrams import I32, Vi

    vi = Vi(C.NAME)
    x = vi.control("x", I32, REQUIRED)
    result = vi.indicator("result", I32)
    node = vi.primitive(99999, [("out", 0, I32, True), ("in", 1, I32, False)])
    vi.wire(x, node["in"])
    vi.wire(node["out"], result)
    project.add(vi)
    package = project.generate(C.NAME)
    with pytest.raises(NotImplementedError):
        package.function(x=1)
