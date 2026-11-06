"""A path as lvkit prints it (``<userlib>/…``) is found in the library folders."""

from pathlib import Path

import pytest

from lvkit.input_path import InputNotFound, resolve_input


def test_first_folder_that_has_the_file(tmp_path: Path) -> None:
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    (d2 / "A").mkdir(parents=True)
    (d2 / "A" / "B.vi").write_bytes(b"")
    assert resolve_input("<userlib>/A/B.vi", {"<userlib>": (d1, d2)}) == (
        d2 / "A" / "B.vi"
    )


def test_order_wins_when_both_have_it(tmp_path: Path) -> None:
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    for d in (d1, d2):
        (d / "A").mkdir(parents=True)
        (d / "A" / "B.vi").write_bytes(b"")
    assert resolve_input("<userlib>/A/B.vi", {"<userlib>": (d1, d2)}) == (
        d1 / "A" / "B.vi"
    )


def test_folder_input(tmp_path: Path) -> None:
    (tmp_path / "A").mkdir()
    assert resolve_input("<userlib>/A", {"<userlib>": (tmp_path,)}) == tmp_path / "A"


def test_unknown_token() -> None:
    with pytest.raises(InputNotFound) as e:
        resolve_input("<resource>/x.vi", {"<vilib>": (Path("/a"),)})
    assert e.value.tried == ()
    assert "no folder" in e.value.reason


def test_flag_named_when_no_folder() -> None:
    with pytest.raises(InputNotFound) as e:
        resolve_input("<userlib>/x.vi", {"<userlib>": ()})
    assert "--userlib" in e.value.reason


def test_missing_file_lists_tried(tmp_path: Path) -> None:
    with pytest.raises(InputNotFound) as e:
        resolve_input("<vilib>/missing.vi", {"<vilib>": (tmp_path,)})
    assert e.value.tried == (tmp_path / "missing.vi",)


def test_plain_paths_unchanged() -> None:
    assert resolve_input("rel/x.vi", {}) == Path("rel/x.vi")
    assert resolve_input("/abs/x.vi", {}) == Path("/abs/x.vi")
