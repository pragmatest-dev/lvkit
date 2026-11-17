"""NI's Configuration File VIs (``<vilib>/Utility/config.llb``): each caller diagram
is generated from the catalog entry the recorded path names and its Python is run
on a temporary .ini file. The refnum is a plain dict, so one call's result feeds the
next call's control.

The terminal indices and labels are those the corpus callers' call nodes save for
each VI's pane (JKI VI Tester, the icon editor, the actor framework)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lvkit.codegen.gaps import Gap
from tests.vilib_caller import (
    BOOL,
    DBL,
    I32,
    PATH,
    STRING,
    U32,
    primitive,
    run_catalog_call,
)

LLB = ["<vilib>", "Utility", "config.llb"]
REFNUM = primitive("Refnum")
ARRAY = primitive("Array")


def _call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    vi: str,
    pane: list[tuple[int, str, Any]],
    inputs: dict[str, tuple[int, Any]],
    outputs: dict[str, tuple[int, Any]],
    **arguments: Any,
) -> Any:
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*LLB, f"{vi}.vi"],
        pane=pane,
        inputs=inputs,
        outputs=outputs,
        arguments=arguments,
    )
    return result


def _open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    vi: str = "Open Config Data",
    **a: Any,
) -> Any:
    wired: dict[str, tuple[int, Any]] = {"path": (11, PATH)}
    if "create" in a:
        wired["create"] = (10, BOOL)
    return _call(
        tmp_path,
        monkeypatch,
        vi,
        [
            (2, "output", BOOL),
            (3, "output", REFNUM),
            (10, "input", BOOL),
            (11, "input", PATH),
        ],
        wired,
        {"refnum": (3, REFNUM), "created": (2, BOOL)},
        **a,
    )


def _read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    lv_type: Any,
    ref: Any,
    key: str,
    section: str = "S",
    **a: Any,
) -> Any:
    pane = [
        (1, "output", lv_type),
        (2, "output", BOOL),
        (3, "output", REFNUM),
        (7, "input", STRING),
        (9, "input", lv_type),
        (10, "input", STRING),
        (11, "input", REFNUM),
    ]
    inputs: dict[str, tuple[int, Any]] = {
        "ref": (11, REFNUM),
        "section": (7, STRING),
        "key": (10, STRING),
    }
    if "default" in a:
        inputs["default"] = (9, lv_type)
    if "raw" in a:
        pane.append((6, "input", BOOL))
        inputs["raw"] = (6, BOOL)
    return _call(
        tmp_path,
        monkeypatch,
        f"Read Key ({kind})",
        pane,
        inputs,
        {"value": (1, lv_type), "found": (2, BOOL)},
        ref=ref,
        section=section,
        key=key,
        **a,
    )


def _write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    lv_type: Any,
    ref: Any,
    key: str,
    value: Any,
    section: str = "S",
    **a: Any,
) -> Any:
    pane = [
        (2, "output", BOOL),
        (3, "output", REFNUM),
        (7, "input", STRING),
        (9, "input", lv_type),
        (10, "input", STRING),
        (11, "input", REFNUM),
    ]
    inputs: dict[str, tuple[int, Any]] = {
        "ref": (11, REFNUM),
        "section": (7, STRING),
        "key": (10, STRING),
        "value": (9, lv_type),
    }
    if "raw" in a:
        pane.append((6, "input", BOOL))
        inputs["raw"] = (6, BOOL)
    return _call(
        tmp_path,
        monkeypatch,
        f"Write Key ({kind})",
        pane,
        inputs,
        {"found": (2, BOOL)},
        ref=ref,
        section=section,
        key=key,
        value=value,
        **a,
    )


def _close(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ref: Any, **a: Any) -> Any:
    inputs: dict[str, tuple[int, Any]] = {"ref": (11, REFNUM)}
    pane = [(3, "output", PATH), (11, "input", REFNUM)]
    if "write" in a:
        pane.append((10, "input", BOOL))
        inputs["write"] = (10, BOOL)
    return _call(
        tmp_path,
        monkeypatch,
        "Close Config Data",
        pane,
        inputs,
        {"path": (3, PATH)},
        ref=ref,
        **a,
    )


def _ini(tmp_path: Path, text: str | None = None) -> Path:
    path = tmp_path / "data.ini"
    if text is not None:
        path.write_text(text, encoding="utf-8")
    return path


def test_open_a_missing_file_creates_it_and_reports_file_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _ini(tmp_path)
    result = _open(tmp_path, monkeypatch, path=path)
    assert result.created is True
    assert path.exists()
    existing = _open(tmp_path, monkeypatch, path=path)
    assert existing.created is False


def test_open_a_missing_file_without_create_is_error_7(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _ini(tmp_path)
    with pytest.raises(FileNotFoundError, match="create file if necessary"):
        _open(tmp_path, monkeypatch, path=path, create=False)
    assert not path.exists()
    with pytest.raises(OSError, match="not a path"):
        _open(tmp_path, monkeypatch, path=Path(""))


def test_open_config_data_compatibility_opens_the_file_the_same_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _ini(tmp_path)
    result = _open(tmp_path, monkeypatch, "Open Config Data (compatibility)", path=path)
    assert result.created is True
    assert path.exists()


def test_read_each_type_from_an_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _ini(
        tmp_path,
        '[S]\ntext="hello world"\nflag=false\nbig=4000000000\nneg=-5\n'
        'ratio = 12.3 ;comm\npth="/tmp/data.dat"\n',
    )
    ref = _open(tmp_path, monkeypatch, path=path).refnum
    text = _read(tmp_path, monkeypatch, "String", STRING, ref, "text", default="d")
    assert (text.value, text.found) == ("hello world", True)
    flag = _read(tmp_path, monkeypatch, "Boolean", BOOL, ref, "flag", default=True)
    assert (flag.value, flag.found) == (False, True)
    big = _read(tmp_path, monkeypatch, "U32", U32, ref, "big", default=0)
    assert (big.value, big.found) == (4000000000, True)
    neg = _read(tmp_path, monkeypatch, "I32", I32, ref, "neg", default=0)
    assert (neg.value, neg.found) == (-5, True)
    ratio = _read(tmp_path, monkeypatch, "Double", DBL, ref, "ratio", default=0.0)
    assert (ratio.value, ratio.found) == (12.3, True)
    pth = _read(tmp_path, monkeypatch, "Path", PATH, ref, "pth")
    assert (pth.value, pth.found) == (Path("/tmp/data.dat"), True)


def test_a_missing_key_or_section_gives_the_default_and_found_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = _open(tmp_path, monkeypatch, path=_ini(tmp_path, "[S]\nk=1\n")).refnum
    for kind, lv_type, default in (
        ("String", STRING, "dflt"),
        ("Boolean", BOOL, True),
        ("Double", DBL, 2.5),
        ("I32", I32, -7),
        ("U32", U32, 9),
    ):
        missing = _read(
            tmp_path, monkeypatch, kind, lv_type, ref, "nope", default=default
        )
        assert (missing.value, missing.found) == (default, False)
        other = _read(
            tmp_path,
            monkeypatch,
            kind,
            lv_type,
            ref,
            "k",
            section="Other",
            default=default,
        )
        assert (other.value, other.found) == (default, False)


def test_an_unwired_default_is_the_empty_value_of_its_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = _open(tmp_path, monkeypatch, path=_ini(tmp_path)).refnum
    assert _read(tmp_path, monkeypatch, "String", STRING, ref, "k").value == ""
    assert _read(tmp_path, monkeypatch, "Boolean", BOOL, ref, "k").value is False
    assert _read(tmp_path, monkeypatch, "I32", I32, ref, "k").value == 0
    assert _read(tmp_path, monkeypatch, "Double", DBL, ref, "k").value == 0.0


def test_section_and_key_names_match_ignoring_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = _open(
        tmp_path, monkeypatch, path=_ini(tmp_path, "[Data]\nMyKey=abc\n")
    ).refnum
    result = _read(
        tmp_path,
        monkeypatch,
        "String",
        STRING,
        ref,
        "mykey",
        section="DATA",
        default="",
    )
    assert (result.value, result.found) == ("abc", True)


def test_ni_table_44_key_value_examples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = (
        "[S]\n"
        "keyname='mystring'\n"
        ';keyname2="mystring"\n'
        'keyname3="my;string"\n'
        "keyname4 = 12.3 ;comm\n"
        'key name6=" mystring6"\n'
        "keyname8 = mystring8\n"
        "keyname12==mystring12\n"
        "#keyname13 = mystring13\n"
        "abc=\n"
    )
    ref = _open(tmp_path, monkeypatch, path=_ini(tmp_path, text)).refnum

    def string(key: str) -> tuple[str, bool]:
        got = _read(tmp_path, monkeypatch, "String", STRING, ref, key, default="d")
        return got.value, got.found

    assert string("keyname") == ("mystring", True)
    assert string("keyname2") == ("d", False)
    assert string("keyname3") == ("my;string", True)
    assert string("keyname4") == ("12.3 ;comm", True)
    assert string("key name6") == (" mystring6", True)
    assert string("keyname8") == ("mystring8", True)
    assert string("keyname12") == ("=mystring12", True)
    assert string("#keyname13") == ("mystring13", True)
    assert string("abc") == ("d", False)
    number = _read(tmp_path, monkeypatch, "Double", DBL, ref, "keyname4", default=0.0)
    assert number.value == 12.3


def test_a_number_or_boolean_in_quotation_marks_is_read_without_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NI: "LabVIEW removes the quotation marks when a VI returns the value"; OpenG
    stores a number as a quoted string (``count="42"``) and reads it back as one."""
    text = '[S]\ni="42"\nu=\'7\'\nd="1.5"\nb="TRUE"\n'
    ref = _open(tmp_path, monkeypatch, path=_ini(tmp_path, text)).refnum
    assert _read(tmp_path, monkeypatch, "I32", I32, ref, "i", default=0).value == 42
    assert _read(tmp_path, monkeypatch, "U32", U32, ref, "u", default=0).value == 7
    double = _read(tmp_path, monkeypatch, "Double", DBL, ref, "d", default=0.0)
    assert double.value == 1.5
    assert _read(tmp_path, monkeypatch, "Boolean", BOOL, ref, "b", default=False).value


def test_boolean_values_are_not_case_sensitive_and_zero_is_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = _open(
        tmp_path,
        monkeypatch,
        path=_ini(tmp_path, "[S]\na=False\nb=0\nc=TRUE\nd=true\n"),
    ).refnum
    for key, expected in (("a", False), ("b", False), ("c", True), ("d", True)):
        got = _read(
            tmp_path, monkeypatch, "Boolean", BOOL, ref, key, default=not expected
        )
        assert (got.value, got.found) == (expected, True)


def test_an_out_of_range_integer_is_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = _open(
        tmp_path, monkeypatch, path=_ini(tmp_path, "[S]\nbig=4000000000\n")
    ).refnum
    assert _read(tmp_path, monkeypatch, "I32", I32, ref, "big", default=3).value == 3


def test_string_escapes_are_read_and_a_raw_read_keeps_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = _open(
        tmp_path, monkeypatch, path=_ini(tmp_path, '[S]\nk="a\\\\b\\0Dc"\n')
    ).refnum
    cooked = _read(tmp_path, monkeypatch, "String", STRING, ref, "k", default="")
    assert cooked.value == "a\\b\rc"
    raw = _read(tmp_path, monkeypatch, "String", STRING, ref, "k", default="", raw=True)
    assert raw.value == "a\\\\b\\0Dc"


def test_write_close_reopen_and_read_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _ini(tmp_path)
    ref = _open(tmp_path, monkeypatch, path=path).refnum
    created = _write(tmp_path, monkeypatch, "String", STRING, ref, "name", "a\\b\tc\nd")
    assert created.found is False
    again = _write(tmp_path, monkeypatch, "String", STRING, ref, "NAME", "e")
    assert again.found is True
    _write(tmp_path, monkeypatch, "String", STRING, ref, "raw", "x\\y", raw=True)
    _write(tmp_path, monkeypatch, "Path", PATH, ref, "where", Path("rel/dir/file.txt"))
    assert path.read_text(encoding="utf-8") == ""
    closed = _close(tmp_path, monkeypatch, ref)
    assert closed.path == path
    assert path.read_text(encoding="utf-8") == (
        '[S]\nname="e"\nraw="x\\y"\nwhere="rel/dir/file.txt"\n'
    )
    back = _open(tmp_path, monkeypatch, path=path).refnum
    assert (
        _read(tmp_path, monkeypatch, "String", STRING, back, "name", default="").value
        == "e"
    )
    assert (
        _read(
            tmp_path, monkeypatch, "String", STRING, back, "raw", default="", raw=True
        ).value
        == "x\\y"
    )
    assert _read(tmp_path, monkeypatch, "Path", PATH, back, "where").value == Path(
        "rel/dir/file.txt"
    )


def test_a_string_with_backslash_and_control_characters_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _ini(tmp_path)
    value = "a = b ; c\\d\tend\nline"
    ref = _open(tmp_path, monkeypatch, path=path).refnum
    _write(tmp_path, monkeypatch, "String", STRING, ref, "k", value)
    _close(tmp_path, monkeypatch, ref)
    assert (
        path.read_text(encoding="utf-8") == '[S]\nk="a = b ; c\\\\d\tend\\0Aline"\n'
    )
    back = _open(tmp_path, monkeypatch, path=path).refnum
    assert (
        _read(tmp_path, monkeypatch, "String", STRING, back, "k", default="").value
        == value
    )


def test_a_windows_style_absolute_path_is_stored_in_the_linux_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _ini(tmp_path)
    ref = _open(tmp_path, monkeypatch, path=path).refnum
    _write(tmp_path, monkeypatch, "Path", PATH, ref, "abs", Path("/c/temp/data.dat"))
    _close(tmp_path, monkeypatch, ref)
    assert 'abs="/c/temp/data.dat"' in path.read_text(encoding="utf-8")


def test_close_with_write_file_if_changed_false_leaves_the_file_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _ini(tmp_path, "[S]\nk=1\n")
    ref = _open(tmp_path, monkeypatch, path=path).refnum
    _write(tmp_path, monkeypatch, "String", STRING, ref, "k", "2")
    _close(tmp_path, monkeypatch, ref, write=False)
    assert path.read_text(encoding="utf-8") == "[S]\nk=1\n"


def test_close_without_a_change_does_not_touch_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _ini(tmp_path, "; a comment\n[S]\nk=1\n")
    ref = _open(tmp_path, monkeypatch, path=path).refnum
    _close(tmp_path, monkeypatch, ref)
    assert path.read_text(encoding="utf-8") == "; a comment\n[S]\nk=1\n"


def test_remove_key_reports_found_and_the_file_drops_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _ini(tmp_path, "[S]\na=1\nb=2\n")
    ref = _open(tmp_path, monkeypatch, path=path).refnum
    pane = [
        (2, "output", BOOL),
        (3, "output", REFNUM),
        (7, "input", STRING),
        (10, "input", STRING),
        (11, "input", REFNUM),
    ]
    inputs = {"ref": (11, REFNUM), "section": (7, STRING), "key": (10, STRING)}
    found = _call(
        tmp_path,
        monkeypatch,
        "Remove Key",
        pane,
        inputs,
        {"found": (2, BOOL)},
        ref=ref,
        section="s",
        key="A",
    )
    missing = _call(
        tmp_path,
        monkeypatch,
        "Remove Key",
        pane,
        inputs,
        {"found": (2, BOOL)},
        ref=ref,
        section="s",
        key="A",
    )
    assert (found.found, missing.found) == (True, False)
    _close(tmp_path, monkeypatch, ref)
    assert path.read_text(encoding="utf-8") == "[S]\nb=2\n"


def test_get_key_names_lists_the_keys_in_file_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = _open(
        tmp_path, monkeypatch, path=_ini(tmp_path, "[S]\nz=1\na=2\n[T]\nq=3\n")
    ).refnum
    pane = [
        (1, "output", ARRAY),
        (2, "output", BOOL),
        (3, "output", REFNUM),
        (7, "input", STRING),
        (11, "input", REFNUM),
    ]
    inputs = {"ref": (11, REFNUM), "section": (7, STRING)}
    outputs = {"names": (1, ARRAY), "exists": (2, BOOL)}
    got = _call(
        tmp_path,
        monkeypatch,
        "Get Key Names",
        pane,
        inputs,
        outputs,
        ref=ref,
        section="S",
    )
    assert (got.names, got.exists) == (["z", "a"], True)
    none = _call(
        tmp_path,
        monkeypatch,
        "Get Key Names",
        pane,
        inputs,
        outputs,
        ref=ref,
        section="X",
    )
    assert (none.names, none.exists) == ([], False)


def _remove_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ref: Any, section: str, key: str
) -> Any:
    return _call(
        tmp_path,
        monkeypatch,
        "Remove Key",
        [
            (2, "output", BOOL),
            (3, "output", REFNUM),
            (7, "input", STRING),
            (10, "input", STRING),
            (11, "input", REFNUM),
        ],
        {"ref": (11, REFNUM), "section": (7, STRING), "key": (10, STRING)},
        {"found": (2, BOOL)},
        ref=ref,
        section=section,
        key=key,
    )


def test_writing_back_keeps_the_comments_blank_lines_and_order_of_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _ini(
        tmp_path,
        "; settings for the app\n"
        "# hash line\n"
        "\n"
        "[First]\n"
        "; about a\n"
        "a=1\n"
        "b = 2 ;c\n"
        "keep=x\n"
        "\n"
        "[Second]\n"
        "z=9\n",
    )
    ref = _open(tmp_path, monkeypatch, path=path).refnum
    _write(tmp_path, monkeypatch, "String", STRING, ref, "A", "new", section="First")
    _write(tmp_path, monkeypatch, "String", STRING, ref, "fresh", "v", section="First")
    _write(tmp_path, monkeypatch, "String", STRING, ref, "t", "v", section="Third")
    assert _remove_key(tmp_path, monkeypatch, ref, "First", "b").found is True
    _close(tmp_path, monkeypatch, ref)
    assert path.read_text(encoding="utf-8") == (
        "; settings for the app\n"
        "# hash line\n"
        "\n"
        "[First]\n"
        "; about a\n"
        'a="new"\n'
        "keep=x\n"
        'fresh="v"\n'
        "\n"
        "[Second]\n"
        "z=9\n"
        "\n"
        "[Third]\n"
        't="v"\n'
    )
    back = _open(tmp_path, monkeypatch, path=path).refnum
    assert _read(tmp_path, monkeypatch, "String", STRING, back, "keep", "First").found


def test_a_changed_key_keeps_the_text_before_its_value_and_a_second_write_is_a_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _ini(tmp_path, "[S]\nk  =  1\nj=2\n")
    ref = _open(tmp_path, monkeypatch, path=path).refnum
    _write(tmp_path, monkeypatch, "I32", I32, ref, "k", 5)
    _close(tmp_path, monkeypatch, ref)
    assert path.read_text(encoding="utf-8") == "[S]\nk  =  5\nj=2\n"
    _write(tmp_path, monkeypatch, "I32", I32, ref, "j", 6)
    _close(tmp_path, monkeypatch, ref)
    assert path.read_text(encoding="utf-8") == "[S]\nk  =  5\nj=6\n"


def test_writing_back_keeps_the_line_endings_of_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "crlf.ini"
    path.write_bytes(b"; c\r\n[S]\r\na=1\r\n")
    ref = _open(tmp_path, monkeypatch, path=path).refnum
    _write(tmp_path, monkeypatch, "I32", I32, ref, "a", 2)
    _write(tmp_path, monkeypatch, "I32", I32, ref, "b", 3)
    _close(tmp_path, monkeypatch, ref)
    assert path.read_bytes() == b"; c\r\n[S]\r\na=2\r\nb=3\r\n"


def test_a_key_with_continuation_lines_is_removed_with_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _ini(tmp_path, "[S]\na=1\n  more\nb=2\n")
    ref = _open(tmp_path, monkeypatch, path=path).refnum
    _remove_key(tmp_path, monkeypatch, ref, "S", "a")
    _close(tmp_path, monkeypatch, ref)
    assert path.read_text(encoding="utf-8") == "[S]\nb=2\n"


def test_write_key_boolean_i32_and_u32_write_their_text_and_read_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _ini(tmp_path)
    ref = _open(tmp_path, monkeypatch, path=path).refnum
    created = _write(tmp_path, monkeypatch, "Boolean", BOOL, ref, "flag", True)
    assert created.found is False
    assert _write(tmp_path, monkeypatch, "Boolean", BOOL, ref, "FLAG", False).found
    _write(tmp_path, monkeypatch, "Boolean", BOOL, ref, "on", True)
    _write(tmp_path, monkeypatch, "I32", I32, ref, "n", -5)
    _write(tmp_path, monkeypatch, "U32", U32, ref, "u", 4000000000)
    _close(tmp_path, monkeypatch, ref)
    assert path.read_text(encoding="utf-8") == (
        "[S]\nflag=FALSE\non=TRUE\nn=-5\nu=4000000000\n"
    )
    back = _open(tmp_path, monkeypatch, path=path).refnum
    flag = _read(tmp_path, monkeypatch, "Boolean", BOOL, back, "on", default=False)
    assert flag.value is True
    number = _read(tmp_path, monkeypatch, "I32", I32, back, "n", default=0)
    assert number.value == -5
    big = _read(tmp_path, monkeypatch, "U32", U32, back, "u", default=0)
    assert big.value == 4000000000


def test_remove_section_reports_found_and_the_file_drops_its_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _ini(tmp_path, "[A]\nx=1\n; kept\n[B]\ny=2\n")
    ref = _open(tmp_path, monkeypatch, path=path).refnum
    pane = [
        (2, "output", BOOL),
        (3, "output", REFNUM),
        (7, "input", STRING),
        (11, "input", REFNUM),
    ]
    inputs = {"ref": (11, REFNUM), "section": (7, STRING)}
    outputs = {"exists": (2, BOOL)}

    def remove() -> Any:
        return _call(
            tmp_path,
            monkeypatch,
            "Remove Section",
            pane,
            inputs,
            outputs,
            ref=ref,
            section="a",
        )

    first, again = remove(), remove()
    assert (first.exists, again.exists) == (True, False)
    _close(tmp_path, monkeypatch, ref)
    assert path.read_text(encoding="utf-8") == "; kept\n[B]\ny=2\n"


def test_write_key_double_stays_a_gap_for_its_unrecorded_extra_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError, match="Write Key"):
        run_catalog_call(
            tmp_path,
            monkeypatch,
            [*LLB, "Write Key (Double).vi"],
            pane=[(3, "output", REFNUM), (11, "input", REFNUM), (9, "input", DBL)],
            inputs={"ref": (11, REFNUM), "value": (9, DBL)},
            outputs={"ref_out": (3, REFNUM)},
            arguments={"ref": None, "value": 1.5},
            gaps=gaps,
            runtime_not_implemented=True,
        )
    assert [gap.node_name for gap in gaps] == ["Write Key (Double).vi"]


def test_a_neighbour_with_no_recorded_pane_stays_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError, match="Get Section Names"):
        run_catalog_call(
            tmp_path,
            monkeypatch,
            [*LLB, "Get Section Names.vi"],
            pane=[(3, "output", REFNUM), (11, "input", REFNUM)],
            inputs={"ref": (11, REFNUM)},
            outputs={"ref_out": (3, REFNUM)},
            arguments={"ref": None},
            gaps=gaps,
            runtime_not_implemented=True,
        )
    assert [gap.node_name for gap in gaps] == ["Get Section Names.vi"]
