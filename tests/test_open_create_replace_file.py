"""Open/Create/Replace File (primitive 8050): the operation and access it is given
choose how the Python file is opened, and the generated code runs against a real
folder. NI, "Open/Create/Replace File Function": operation 0 open, 1 replace,
2 create (error 10 if the file exists), 3 open or create, 4 replace or create, 5
with confirmation; access 0 read/write, 1 read-only, 2 write-only; defaults open
and read/write; error 7 when the file cannot be found."""

from __future__ import annotations

import inspect
import io
from pathlib import Path
from typing import Any

import pytest

from tests.error_diagrams import (
    BOOL,
    ERR,
    FILE,
    I32,
    PATH,
    REQUIRED,
    Package,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)
from tests.live_io_recorder import PromptRecorder

# How an input is given: left unwired, wired to a constant, or to a control.
WIRED = "wired"
OPEN, REPLACE, CREATE, OPEN_OR_CREATE, REPLACE_OR_CREATE, CONFIRM = range(6)
READ_WRITE, READ_ONLY, WRITE_ONLY = range(3)


def _node(
    project: Project,  # noqa: F811
    operation: int | str | None = None,
    access: int | str | None = None,
    buffering: bool | str | None = None,
) -> str:
    vi = Vi("Err.vi")
    path = vi.control("path", PATH, REQUIRED)
    ein = vi.error_in()
    refnum = vi.indicator("refnum", FILE)
    cancelled = vi.indicator("cancelled", BOOL)
    eout = vi.error_out()
    controls: dict[str, int] = {}
    node = vi.primitive(
        8050,
        [
            ("disable buffering", 6, BOOL, False),
            ("error in", 8, ERR, False),
            ("access", 9, I32, False),
            ("operation", 10, I32, False),
            ("file path", 11, PATH, False),
            ("error out", 0, ERR, True),
            ("cancelled", 1, BOOL, True),
            ("refnum", 3, FILE, True),
        ],
    )
    for label, value, kind in (
        ("operation", operation, I32),
        ("access", access, I32),
        ("buffering", buffering, BOOL),
    ):
        if value == WIRED:
            controls[label] = vi.control(label, kind, REQUIRED)
        elif value is not None:
            controls[label] = (
                vi.bool_constant(bool(value))
                if kind == BOOL
                else vi.int_constant(int(value))
            )
    vi.wire(path, node["file path"])
    vi.wire(ein, node["error in"])
    if "operation" in controls:
        vi.wire(controls["operation"], node["operation"])
    if "access" in controls:
        vi.wire(controls["access"], node["access"])
    if "buffering" in controls:
        vi.wire(controls["buffering"], node["disable buffering"])
    vi.wire(node["refnum"], refnum)
    vi.wire(node["cancelled"], cancelled)
    vi.wire(node["error out"], eout)
    project.add(vi)
    return "Err.vi"


def _generate(project: Project, **given: Any) -> Package:  # noqa: F811
    return project.generate(_node(project, **given), None)


def _open(package: Package, path: Path, **inputs: Any) -> Any:
    return package.function(path=path, **inputs)


def _there(tmp_path: Path) -> Path:
    path = tmp_path / "there.bin"
    path.write_bytes(b"old")
    return path


# =============================================================
# operation x existing / missing file, access read/write
# =============================================================

# operation -> (existing file: (error code, contents after), missing: (code, file made))
OUTCOMES = {
    OPEN: ((None, b"old"), (7, False)),
    REPLACE: ((None, b""), (7, False)),
    CREATE: ((10, b"old"), (None, True)),
    OPEN_OR_CREATE: ((None, b"old"), (None, True)),
    REPLACE_OR_CREATE: ((None, b""), (None, True)),
}


def _check(result: Any, path: Path, code: int | None, made: bool | bytes) -> None:
    if code is None:
        assert result.error_out is None
        assert result.refnum is not None
        result.refnum.close()
    else:
        assert result.error_out.code == code
        assert result.refnum is None
    if isinstance(made, bytes):
        assert path.read_bytes() == made
    else:
        assert path.exists() is made


@pytest.mark.parametrize("operation", sorted(OUTCOMES))
@pytest.mark.parametrize("as_constant", [True, False])
def test_every_operation_on_an_existing_file(
    project: Project,  # noqa: F811
    tmp_path: Path,
    operation: int,
    as_constant: bool,
) -> None:
    package = _generate(project, operation=operation if as_constant else WIRED)
    path = _there(tmp_path)
    inputs = {} if as_constant else {"operation": operation}
    code, contents = OUTCOMES[operation][0]
    _check(_open(package, path, **inputs), path, code, contents)


@pytest.mark.parametrize("operation", sorted(OUTCOMES))
@pytest.mark.parametrize("as_constant", [True, False])
def test_every_operation_on_a_missing_file(
    project: Project,  # noqa: F811
    tmp_path: Path,
    operation: int,
    as_constant: bool,
) -> None:
    package = _generate(project, operation=operation if as_constant else WIRED)
    path = tmp_path / "missing.bin"
    inputs = {} if as_constant else {"operation": operation}
    code, made = OUTCOMES[operation][1]
    _check(_open(package, path, **inputs), path, code, made)


def test_an_unwired_operation_and_access_are_open_and_read_write(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _generate(project)
    path = _there(tmp_path)
    result = _open(package, path)
    assert result.error_out is None
    assert result.refnum.readable() and result.refnum.writable()
    assert result.refnum.read() == b"old"
    result.refnum.close()
    assert _open(package, tmp_path / "missing.bin").error_out.code == 7


# =============================================================
# access
# =============================================================


@pytest.mark.parametrize("as_constant", [True, False])
def test_read_only_reads_and_refuses_to_write(
    project: Project,  # noqa: F811
    tmp_path: Path,
    as_constant: bool,
) -> None:
    package = _generate(project, access=READ_ONLY if as_constant else WIRED)
    path = _there(tmp_path)
    inputs = {} if as_constant else {"access": READ_ONLY}
    refnum = _open(package, path, **inputs).refnum
    assert refnum.read() == b"old"
    with pytest.raises(io.UnsupportedOperation):
        refnum.write(b"x")
    refnum.close()


@pytest.mark.parametrize("as_constant", [True, False])
def test_write_only_writes_without_replacing_and_refuses_to_read(
    project: Project,  # noqa: F811
    tmp_path: Path,
    as_constant: bool,
) -> None:
    package = _generate(project, access=WRITE_ONLY if as_constant else WIRED)
    path = _there(tmp_path)
    inputs = {} if as_constant else {"access": WRITE_ONLY}
    refnum = _open(package, path, **inputs).refnum
    with pytest.raises(io.UnsupportedOperation):
        refnum.read()
    refnum.write(b"N")
    refnum.close()
    assert path.read_bytes() == b"Nld"


def test_read_write_writes_then_reads_the_same_file(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _generate(project, operation=REPLACE_OR_CREATE, access=READ_WRITE)
    path = tmp_path / "new.bin"
    refnum = _open(package, path).refnum
    refnum.write(b"hello")
    refnum.seek(0)
    assert refnum.read() == b"hello"
    refnum.close()


# =============================================================
# disable buffering
# =============================================================


@pytest.mark.parametrize("buffering", [True, False])
@pytest.mark.parametrize("as_constant", [True, False])
def test_disable_buffering_opens_an_unbuffered_file(
    project: Project,  # noqa: F811
    tmp_path: Path,
    buffering: bool,
    as_constant: bool,
) -> None:
    package = _generate(project, buffering=buffering if as_constant else WIRED)
    inputs = {} if as_constant else {"buffering": buffering}
    refnum = _open(package, _there(tmp_path), **inputs).refnum
    assert isinstance(refnum, io.RawIOBase) is buffering
    refnum.close()


# =============================================================
# refusals
# =============================================================


@pytest.mark.parametrize(
    ("operation", "access"),
    [(7, READ_WRITE), (REPLACE, READ_ONLY), (OPEN, 3), (CONFIRM, READ_ONLY)],
)
def test_a_wired_value_that_cannot_be_honoured_raises_naming_it(
    project: Project,  # noqa: F811
    tmp_path: Path,
    operation: int,
    access: int,
) -> None:
    package = _generate(project, operation=WIRED, access=WIRED)
    with pytest.raises(ValueError) as raised:
        _open(package, _there(tmp_path), operation=operation, access=access)
    assert f"operation {operation!r} with access {access!r}" in str(raised.value)


@pytest.mark.parametrize(
    ("operation", "access", "reason"),
    [
        (CONFIRM, READ_ONLY, "read-only"),
        (REPLACE, READ_ONLY, "read-only"),
        (REPLACE_OR_CREATE, READ_ONLY, "read-only"),
    ],
)
def test_a_constant_that_cannot_be_honoured_is_a_gap(
    project: Project,  # noqa: F811
    operation: int,
    access: int | None,
    reason: str,
) -> None:
    vi_name = _node(project, operation=operation, access=access)
    package = project.generate(vi_name, None)
    assert any(reason in str(gap) for gap in package.gaps)


# =============================================================
# operation 5, replace or create with confirmation
# =============================================================


@pytest.mark.parametrize("as_constant", [True, False])
def test_confirmation_replaces_an_existing_file_when_replace_is_chosen(
    project: Project,  # noqa: F811
    tmp_path: Path,
    as_constant: bool,
) -> None:
    package = _generate(project, operation=CONFIRM if as_constant else WIRED)
    path = _there(tmp_path)
    recorder = PromptRecorder(0)
    inputs = {} if as_constant else {"operation": CONFIRM}
    result = _open(package, path, live_io=recorder, **inputs)
    assert recorder.dialogs == [
        (
            f"The file {path} already exists. Do you want to replace it?",
            ["Replace", "Cancel"],
            "Open/Create/Replace File",
        )
    ]
    _check(result, path, None, b"")
    assert result.cancelled is False


@pytest.mark.parametrize("as_constant", [True, False])
def test_confirmation_cancel_is_error_43_and_leaves_the_file_unopened(
    project: Project,  # noqa: F811
    tmp_path: Path,
    as_constant: bool,
) -> None:
    package = _generate(project, operation=CONFIRM if as_constant else WIRED)
    path = _there(tmp_path)
    inputs = {} if as_constant else {"operation": CONFIRM}
    result = _open(package, path, live_io=PromptRecorder(1), **inputs)
    assert result.error_out.code == 43
    assert result.error_out.message
    assert result.cancelled is True
    _check(result, path, 43, b"old")


@pytest.mark.parametrize("as_constant", [True, False])
def test_confirmation_creates_a_missing_file_without_asking(
    project: Project,  # noqa: F811
    tmp_path: Path,
    as_constant: bool,
) -> None:
    package = _generate(project, operation=CONFIRM if as_constant else WIRED)
    path = tmp_path / "missing.bin"
    recorder = PromptRecorder(1)
    inputs = {} if as_constant else {"operation": CONFIRM}
    result = _open(package, path, live_io=recorder, **inputs)
    assert recorder.dialogs == []
    _check(result, path, None, True)
    assert result.cancelled is False


def test_a_wired_operation_other_than_5_never_asks(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _generate(project, operation=WIRED)
    recorder = PromptRecorder(1)
    result = _open(
        package, _there(tmp_path), live_io=recorder, operation=REPLACE_OR_CREATE
    )
    assert recorder.dialogs == []
    assert result.error_out is None
    result.refnum.close()


@pytest.mark.parametrize(
    ("operation", "takes_live_io"),
    [
        (None, False),
        (OPEN, False),
        (REPLACE_OR_CREATE, False),
        (CONFIRM, True),
        (WIRED, True),
    ],
)
def test_only_a_node_that_can_ask_gives_its_vi_the_live_io_parameter(
    project: Project,  # noqa: F811
    operation: int | str | None,
    takes_live_io: bool,
) -> None:
    package = _generate(project, operation=operation)
    parameters = inspect.signature(package.function).parameters
    assert ("live_io" in parameters) is takes_live_io


def test_confirmation_with_no_live_io_and_no_terminal_names_the_dialog(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _generate(project, operation=CONFIRM)
    path = _there(tmp_path)
    with pytest.raises(RuntimeError, match="Open/Create/Replace File") as raised:
        _open(package, path)
    assert type(raised.value).__name__ == "PromptUnavailableError"
    assert path.read_bytes() == b"old"


def test_a_relative_path_is_error_1430(
    project: Project,  # noqa: F811
) -> None:
    package = _generate(project)
    result = _open(package, Path("relative.bin"))
    assert result.error_out.code == 1430
    assert result.refnum is None


def _open_then_write(project: Project) -> str:  # noqa: F811
    """Open/Create/Replace File whose refnum is written to by Write to Binary
    File, with the open node's error out as the write's error in."""
    vi = Vi("Err.vi")
    path = vi.control("path", PATH, REQUIRED)
    access = vi.control("access", I32, REQUIRED)
    data = vi.control("data", I32, REQUIRED)
    ein = vi.error_in()
    eout = vi.error_out()
    opening = vi.primitive(
        8050,
        [
            ("error in", 8, ERR, False),
            ("access", 9, I32, False),
            ("file path", 11, PATH, False),
            ("error out", 0, ERR, True),
            ("refnum", 3, FILE, True),
        ],
    )
    writing = vi.primitive(
        8078,
        [
            ("error in", 8, ERR, False),
            ("data", 10, I32, False),
            ("file", 11, FILE, False),
            ("error out", 0, ERR, True),
        ],
    )
    vi.wire(path, opening["file path"])
    vi.wire(access, opening["access"])
    vi.wire(ein, opening["error in"])
    vi.wire(opening["refnum"], writing["file"])
    vi.wire(opening["error out"], writing["error in"])
    vi.wire(data, writing["data"])
    vi.wire(writing["error out"], eout)
    project.add(vi)
    return "Err.vi"


def test_writing_to_a_read_only_file_is_error_8_and_to_a_read_write_one_works(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = project.generate(_open_then_write(project), None)
    path = tmp_path / "data.bin"
    path.write_bytes(b"")
    refused = package.function(path=path, access=READ_ONLY, data=1)
    assert refused.error_out.code == 8
    assert refused.error_out.source == "Write to Binary File"
    assert path.read_bytes() == b""
    assert package.function(path=path, access=READ_WRITE, data=1).error_out is None
    assert path.read_bytes() == b"\x00\x00\x00\x01"
