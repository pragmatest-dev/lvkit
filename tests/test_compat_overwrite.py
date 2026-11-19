"""compatOverwrite.vi (``<vilib>/_oldvers/_oldvers.llb``) feeding the operation of
Open/Create/Replace File (primitive 8050), as OpenG Write GIF File (Animated) and
JKI Prompt Web Browser Path wire it: overwrite (F) TRUE gives operation 4 (replace
or create, no question), FALSE gives 5 (replace or create with confirmation).
The generated caller is run against a real folder."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

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


def _generate(project: Project) -> Package:  # noqa: F811
    vi = Vi("Err.vi")
    path = vi.control("path", PATH, REQUIRED)
    overwrite = vi.control("overwrite", BOOL, REQUIRED)
    ein = vi.error_in()
    refnum = vi.indicator("refnum", FILE)
    cancelled = vi.indicator("cancelled", BOOL)
    eout = vi.error_out()
    compat = vi.call(
        "compatOverwrite.vi",
        [("overwrite?", 0, I32, True), ("overwrite (F)", 1, BOOL, False)],
        vilib="_oldvers/_oldvers.llb",
    )
    node = vi.primitive(
        8050,
        [
            ("error in", 8, ERR, False),
            ("operation", 10, I32, False),
            ("file path", 11, PATH, False),
            ("error out", 0, ERR, True),
            ("cancelled", 1, BOOL, True),
            ("refnum", 3, FILE, True),
        ],
    )
    vi.wire(overwrite, compat["overwrite (F)"])
    vi.wire(compat["overwrite?"], node["operation"])
    vi.wire(path, node["file path"])
    vi.wire(ein, node["error in"])
    closing = vi.primitive(
        8052,
        [
            ("error in", 4, ERR, False),
            ("refnum", 7, FILE, False),
            ("error out", 0, ERR, True),
        ],
    )
    vi.wire(node["refnum"], refnum, closing["refnum"])
    vi.wire(node["cancelled"], cancelled)
    vi.wire(node["error out"], closing["error in"])
    vi.wire(closing["error out"], eout)
    project.add(vi)
    return project.generate("Err.vi", None)


def _there(tmp_path: Path) -> Path:
    path = tmp_path / "there.bin"
    path.write_bytes(b"old")
    return path


def _run(package: Package, path: Path, recorder: PromptRecorder, **given: Any) -> Any:
    return package.function(path=path, live_io=recorder, **given)


def test_overwrite_true_replaces_without_asking(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _generate(project)
    path = _there(tmp_path)
    recorder = PromptRecorder(1)
    result = _run(package, path, recorder, overwrite=True)
    assert recorder.dialogs == []
    assert result.error_out is None
    result.refnum.close()
    assert path.read_bytes() == b""


def test_overwrite_false_asks_and_replace_replaces(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _generate(project)
    path = _there(tmp_path)
    recorder = PromptRecorder(0)
    result = _run(package, path, recorder, overwrite=False)
    assert len(recorder.dialogs) == 1
    assert result.error_out is None
    result.refnum.close()
    assert path.read_bytes() == b""


def test_overwrite_false_and_cancel_is_error_43_and_keeps_the_file(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _generate(project)
    path = _there(tmp_path)
    recorder = PromptRecorder(1)
    result = _run(package, path, recorder, overwrite=False)
    assert len(recorder.dialogs) == 1
    assert result.error_out.code == 43
    assert result.cancelled is True
    assert result.refnum is None
    assert path.read_bytes() == b"old"


def test_overwrite_false_with_a_missing_file_creates_it_without_asking(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _generate(project)
    path = tmp_path / "missing.bin"
    recorder = PromptRecorder(1)
    result = _run(package, path, recorder, overwrite=False)
    assert recorder.dialogs == []
    assert result.error_out is None
    result.refnum.close()
    assert path.exists()


def test_the_caller_takes_live_io_and_imports_nothing_from_lvkit(
    project: Project,  # noqa: F811
) -> None:
    package = _generate(project)
    assert "live_io" in inspect.signature(package.function).parameters
    assert "lvkit" not in package.source
