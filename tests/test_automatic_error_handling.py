"""A node whose error out is not wired follows the VI's automatic error handling
(VI Properties > Execution, ``DefaultErrorHandling``): on, its error is raised;
off, it is dropped and the run continues. A wired error out is a value either
way. Each test builds a tiny diagram, generates its Python and runs it."""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from lvkit.parser.metadata import _parse_lvsr_properties
from tests.error_diagrams import (
    ERR,
    I32,
    PATH,
    REQUIRED,
    Package,
    Project,
    Vi,
    failing_step,
    ok_step,
    project,  # noqa: F401  (the fixture)
    skipping_step,
    step_vi,
)

NAME = "Remove.vi"
STEP = "Step A.vi"


def _delete(project: Project, handling: str, *, wire_error: bool) -> Package:  # noqa: F811
    """Delete (1417) of a path control, its deleted path wired, its error out
    wired to the VI's error out only when ``wire_error``."""
    vi = Vi(NAME)
    vi.execution2 = {"DefaultErrorHandling": handling}
    target = vi.control("target", PATH, REQUIRED)
    deleted = vi.indicator("deleted", PATH)
    out = vi.error_out() if wire_error else None
    node = vi.primitive(
        1417,
        [
            ("error out", 0, ERR, True),
            ("dup path", 1, PATH, True),
            ("error in", 2, ERR, False),
            ("path", 3, PATH, False),
        ],
    )
    vi.wire(target, node["path"])
    vi.wire(node["dup path"], deleted)
    if out is not None:
        vi.wire(node["error out"], out)
    project.add(vi)
    return project.generate(NAME)


def _call_step(
    project: Project,  # noqa: F811
    handling: str,
    stub: str,
    *,
    wire_error: bool,
) -> Package:
    """A call of the subVI ``Step A`` (x, error in -> r, error out) that the
    ``stub`` module replaces, its error out wired to the VI's error out only when
    ``wire_error``."""
    vi = Vi(NAME)
    vi.execution2 = {"DefaultErrorHandling": handling}
    x = vi.control("x", I32, REQUIRED)
    result = vi.indicator("result", I32)
    out = vi.error_out() if wire_error else None
    node = vi.step(STEP)
    vi.wire(x, node["x"])
    vi.wire(node["r"], result)
    if out is not None:
        vi.wire(node["error out"], out)
    project.add(vi)
    project.add(step_vi(STEP))
    return project.generate(NAME, {"step_a": stub})


@pytest.mark.parametrize(("value", "expected"), [("1", True), ("0", False)])
def test_the_setting_is_read_from_the_vi(value: str, expected: bool) -> None:
    root = ET.fromstring(
        "<RSRC><LVSR><Section><Execution />"
        f'<Execution2 DefaultErrorHandling="{value}" />'
        "</Section></LVSR></RSRC>"
    )
    parsed = _parse_lvsr_properties(root)["execution"]["auto_error_handling"]
    assert parsed is expected


def test_a_vi_with_no_readable_setting_has_it_unknown() -> None:
    root = ET.fromstring("<RSRC><LVSR><Section /></LVSR></RSRC>")
    assert _parse_lvsr_properties(root)["execution"]["auto_error_handling"] is None


def test_a_vi_with_an_unknown_setting_raises_like_one_with_it_on(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    x = vi.control("x", I32, REQUIRED)
    result = vi.indicator("result", I32)
    node = vi.step(STEP)
    vi.wire(x, node["x"])
    vi.wire(node["r"], result)
    project.add(vi)
    project.add(step_vi(STEP))
    package = project.generate(NAME, {"step_a": failing_step("step_a", 7)})
    with pytest.raises(package.errors.LabVIEWError):
        package.function(x=1)


def test_a_failed_node_with_unwired_error_out_raises_when_handling_is_on(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _delete(project, "1", wire_error=False)
    with pytest.raises(FileNotFoundError):
        package.function(tmp_path / "missing")


def test_a_failed_node_with_unwired_error_out_continues_when_handling_is_off(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _delete(project, "0", wire_error=False)
    assert package.function(tmp_path / "missing").deleted == Path("")
    file = tmp_path / "f.txt"
    file.write_text("x", encoding="utf-8")
    assert package.function(file).deleted == file
    assert not file.exists()


@pytest.mark.parametrize("handling", ["1", "0"])
def test_a_wired_error_out_is_a_value_either_way(
    project: Project,  # noqa: F811
    tmp_path: Path,
    handling: str,
) -> None:
    package = _delete(project, handling, wire_error=True)
    assert package.function(tmp_path / "missing").error_out.code == 7


def test_an_unwired_error_out_of_a_subvi_call_raises_when_handling_is_on(
    project: Project,  # noqa: F811
) -> None:
    package = _call_step(project, "1", failing_step("step_a", 7), wire_error=False)
    with pytest.raises(package.errors.LabVIEWError) as raised:
        package.function(x=1)
    assert raised.value.code == 7


def test_an_unwired_error_out_of_a_subvi_call_is_dropped_when_handling_is_off(
    project: Project,  # noqa: F811
) -> None:
    package = _call_step(project, "0", failing_step("step_a", 7), wire_error=False)
    assert package.function(x=1).result == 0


@pytest.mark.parametrize("handling", ["1", "0"])
def test_a_subvi_call_that_succeeds_does_not_raise(
    project: Project,  # noqa: F811
    handling: str,
) -> None:
    package = _call_step(project, handling, ok_step("step_a"), wire_error=False)
    assert package.function(x=1).result == 2


def test_an_error_that_only_passed_through_a_subvi_call_is_not_its_own(
    project: Project,  # noqa: F811
) -> None:
    """The call's error in is wired and its error out is not: the callee skips
    on the incoming error and passes it on, which is not an error of the call."""
    vi = Vi(NAME)
    x = vi.control("x", I32, REQUIRED)
    ein = vi.error_in()
    result = vi.indicator("result", I32)
    node = vi.step(STEP)
    vi.wire(x, node["x"])
    vi.wire(ein, node["error in"])
    vi.wire(node["r"], result)
    project.add(vi)
    project.add(step_vi(STEP))
    package = project.generate(NAME, {"step_a": skipping_step("step_a")})
    assert package.function(x=1, error_in=package.error(7)).result == 0
    assert package.function(x=1).result == 2


@pytest.mark.parametrize("handling", ["1", "0"])
def test_a_wired_error_out_of_a_subvi_call_is_a_value_either_way(
    project: Project,  # noqa: F811
    handling: str,
) -> None:
    package = _call_step(project, handling, failing_step("step_a", 7), wire_error=True)
    assert package.function(x=1).error_out.code == 7
