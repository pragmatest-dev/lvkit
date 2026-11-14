"""Stop (1350), Quit LabVIEW (1352), Temporary and Default Directory (1435, 1427),
Set File Size (8074) and Set Permissions (8076)."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path

import pytest

from tests.helpers import error_cluster_type, install_generated_package
from tests.prim_vi import Pin, generate, scalar


def _stop(prim_id: int, name: str, wired: str | None):
    controls = {}
    pin = Pin(0, "input", scalar("Boolean"), source=None)
    if wired == "bool":
        controls["flag"] = scalar("Boolean")
        pin = Pin(0, "input", scalar("Boolean"), source="flag")
    if wired == "error":
        controls["flag"] = error_cluster_type()
        pin = Pin(0, "input", error_cluster_type(), source="flag")
    return generate(prim_id, name, [pin], controls, {})


@pytest.mark.parametrize(("prim_id", "name"), [(1350, "Stop"), (1352, "Quit LabVIEW")])
def test_unwired_stops_the_program(prim_id: int, name: str) -> None:
    source, fn = _stop(prim_id, name, None)
    assert "lvkit" not in source
    with pytest.raises(SystemExit):
        fn()


@pytest.mark.parametrize(("prim_id", "name"), [(1350, "Stop"), (1352, "Quit LabVIEW")])
def test_stops_only_when_the_boolean_is_true(prim_id: int, name: str) -> None:
    _, fn = _stop(prim_id, name, "bool")
    fn(False)
    with pytest.raises(SystemExit):
        fn(True)


@pytest.mark.parametrize(("prim_id", "name"), [(1350, "Stop"), (1352, "Quit LabVIEW")])
def test_an_error_wired_to_it_passes_its_status(prim_id: int, name: str) -> None:
    install_generated_package()
    from generated_package.errors import LabVIEWError

    _, fn = _stop(prim_id, name, "error")
    fn(None)
    with pytest.raises(SystemExit):
        fn(LabVIEWError(code=5, source="x", message=""))


def test_temporary_directory() -> None:
    source, fn = generate(
        1435,
        "Temporary Directory",
        [Pin(0, "output", scalar("Path"), sink="path")],
        {},
        {"path": scalar("Path")},
    )
    assert "lvkit" not in source
    assert fn().path == Path(tempfile.gettempdir())


def test_default_directory_is_a_gap_with_its_reason() -> None:
    _, fn = generate(
        1427,
        "Default Directory",
        [Pin(0, "output", scalar("Path"), sink="path")],
        {},
        {"path": scalar("Path")},
    )
    with pytest.raises(NotImplementedError, match="current directory"):
        fn()


def _set_size(tmp_path: Path, refnum_type=None):
    refnum = refnum_type or scalar("Refnum")
    return generate(
        8074,
        "Set File Size",
        [
            Pin(7, "input", refnum, source="file"),
            Pin(6, "input", scalar("NumInt64"), source="size"),
            Pin(3, "output", refnum, sink="out"),
        ],
        {"file": refnum, "size": scalar("NumInt64")},
        {"out": refnum},
    )


def test_set_file_size_shrinks_and_extends(tmp_path: Path) -> None:
    source, fn = _set_size(tmp_path)
    assert "lvkit" not in source
    target = tmp_path / "data.bin"
    target.write_bytes(b"0123456789")
    with open(target, "r+b") as handle:
        assert fn(handle, 4).out is handle
    assert target.read_bytes() == b"0123"
    with open(target, "r+b") as handle:
        fn(handle, 6)
    assert target.read_bytes() == b"0123\x00\x00"


def test_set_file_size_with_a_path_is_a_gap(tmp_path: Path) -> None:
    _, fn = _set_size(tmp_path, scalar("Path"))
    with pytest.raises(NotImplementedError, match="file path"):
        fn(tmp_path / "x", 1)


def _permissions(**wired: bool):
    controls = {"path": scalar("Path")}
    pins = [
        Pin(11, "input", scalar("Path"), source="path"),
        Pin(
            7, "input", scalar("NumInt16"), source="mode" if wired.get("mode") else None
        ),
        Pin(
            10,
            "input",
            scalar("String"),
            source="owner" if wired.get("owner") else None,
        ),
        Pin(
            9, "input", scalar("String"), source="group" if wired.get("group") else None
        ),
        Pin(3, "output", scalar("Path"), sink="out"),
    ]
    for name, kind in (("mode", "NumInt16"), ("owner", "String"), ("group", "String")):
        if wired.get(name):
            controls[name] = scalar(kind)
    return generate(8076, "Set Permissions", pins, controls, {"out": scalar("Path")})


@pytest.mark.skipif(os.name != "posix", reason="Linux permission bits")
def test_set_permissions_sets_the_nine_bits(tmp_path: Path) -> None:
    source, fn = _permissions(mode=True)
    assert "lvkit" not in source
    target = tmp_path / "f"
    target.write_text("x")
    assert fn(target, 0o640).out == target
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


@pytest.mark.skipif(os.name != "posix", reason="Linux permission bits")
def test_unwired_permissions_leave_them_unchanged(tmp_path: Path) -> None:
    _, fn = _permissions()
    target = tmp_path / "f"
    target.write_text("x")
    target.chmod(0o604)
    fn(target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o604


@pytest.mark.skipif(os.name != "posix", reason="owner and group are not set on Windows")
def test_empty_owner_and_group_are_not_set(tmp_path: Path, monkeypatch) -> None:
    import shutil

    calls: list[tuple] = []
    monkeypatch.setattr(shutil, "chown", lambda *args: calls.append(args))
    _, fn = _permissions(owner=True, group=True)
    target = tmp_path / "f"
    target.write_text("x")
    fn(target, "", "")
    assert calls == []
    fn(target, "alice", "")
    fn(target, "", "staff")
    if sys.platform != "darwin":
        assert calls == [(target, "alice", None), (target, None, "staff")]
