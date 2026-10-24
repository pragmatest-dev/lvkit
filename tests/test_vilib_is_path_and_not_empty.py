"""Is Path and Not Empty: a caller records the VI by the file name NI ships it
under, without the ``?`` of its display name."""

from __future__ import annotations

from lvkit.vilib_resolver import get_resolver


def test_is_path_and_not_empty_resolves_by_its_file_name() -> None:
    entry = get_resolver().resolve_by_name("Is Path and Not Empty.vi")
    assert entry is not None
    assert entry.name == "Is Path and Not Empty?"
    # Indices from the caller's wiring (Get User Interface Details.vi): the path
    # input sits on connector slot 4 and the Boolean output on slot 1.
    assert [(t.name, t.direction, t.index) for t in entry.terminals] == [
        ("path", "in", 4),
        ("Is Path and Not Empty?", "out", 1),
    ]


def test_is_path_and_not_empty_has_inline_python() -> None:
    entry = get_resolver().resolve_by_name("Is Path and Not Empty.vi")
    assert entry is not None and entry.inline
    assert entry.python_code == (
        "{is_path_and_not_empty} = {path} is not None and len(str({path})) > 0"
    )
