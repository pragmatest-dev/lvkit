"""NI's Windows Registry Access VIs (``<vilib>/registry/registry.llb``): each caller
diagram is generated from the catalog entry the recorded path names and its Python
is run. Three ways: on a system that is not Windows (the platform refusal), against
a fake ``winreg`` module (the flow and the error codes, on any system), and, on
Windows only, against the real registry under ``HKEY_CURRENT_USER\\Software``.

The terminal indices and labels are those the corpus callers' call nodes save for
each VI's pane (the icon editor's Populate Font ComboBox, JKI VI Tester's JKI Get
System Web Browser)."""

from __future__ import annotations

import ctypes
import sys
import types
import uuid
from pathlib import Path
from typing import Any

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.vilib_caller import (
    BOOL,
    I32,
    STRING,
    U32,
    primitive,
    run_catalog_call,
)
from tests.vilib_caller import ERROR as PLAIN_CLUSTER

LLB = ["<vilib>", "registry", "registry.llb"]
REFNUM = primitive("Refnum")
ARRAY = primitive("Array")
CLUSTER = PLAIN_CLUSTER
ERROR = LVType(
    kind=LVTypeKind.CLUSTER,
    underlying_type="Cluster",
    fields=[
        ClusterField(name="status", type=BOOL),
        ClusterField(name="code", type=I32),
        ClusterField(name="source", type=STRING),
    ],
)
U16 = primitive("NumUInt16")

HKEY_CLASSES_ROOT = 0x80000000
HKEY_CURRENT_USER = 0x80000001
HKEY_LOCAL_MACHINE = 0x80000002

ROOT_USER, ROOT_MACHINE = 1, 2
MASK_READ, MASK_READ_WRITE = 6, 9

OPEN_PANE = [
    (0, "output", ERROR),
    (3, "output", REFNUM),
    (6, "input", U16),
    (7, "input", STRING),
    (8, "input", ERROR),
    (9, "input", U32),
    (10, "input", STRING),
    (11, "input", U32),
]


def _run(
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
        runtime_not_implemented=True,
    )
    return result


def _open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **a: Any) -> Any:
    wired: dict[str, tuple[int, Any]] = {
        "subkey": (10, STRING),
        "root": (11, U32),
    }
    for name, index, lv_type in (
        ("mask", 9, U32),
        ("view", 6, U16),
        ("machine", 7, STRING),
    ):
        if name in a:
            wired[name] = (index, lv_type)
    return _run(
        tmp_path,
        monkeypatch,
        "Open Registry Key",
        OPEN_PANE,
        wired,
        {"refnum": (3, REFNUM), "error_out": (0, ERROR)},
        **a,
    )


def _close(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ref: Any) -> Any:
    return _run(
        tmp_path,
        monkeypatch,
        "Close Registry Key",
        [(0, "output", ERROR), (4, "input", ERROR), (7, "input", REFNUM)],
        {"ref": (7, REFNUM)},
        {"error_out": (0, ERROR)},
        ref=ref,
    )


def _read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ref: Any, **a: Any) -> Any:
    return _run(
        tmp_path,
        monkeypatch,
        "Read Registry Value Simple STR",
        [
            (0, "output", ERROR),
            (1, "output", STRING),
            (2, "output", U16),
            (3, "output", REFNUM),
            (6, "input", ERROR),
            (7, "input", STRING),
            (8, "input", STRING),
            (9, "input", REFNUM),
        ],
        {"ref": (9, REFNUM), "name": (8, STRING), "default": (7, STRING)},
        {
            "data": (1, STRING),
            "kind": (2, U16),
            "error_out": (0, ERROR),
        },
        ref=ref,
        **a,
    )


def _query(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ref: Any) -> Any:
    return _run(
        tmp_path,
        monkeypatch,
        "Query Registry Key Info",
        [
            (0, "output", ERROR),
            (1, "output", CLUSTER),
            (2, "output", STRING),
            (3, "output", REFNUM),
            (4, "output", CLUSTER),
            (8, "input", ERROR),
            (11, "input", REFNUM),
        ],
        {"ref": (11, REFNUM)},
        {
            "value_info": (1, CLUSTER),
            "key_class": (2, STRING),
            "subkey_info": (4, CLUSTER),
            "error_out": (0, ERROR),
        },
        ref=ref,
    )


def _enum(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ref: Any,
    value_info: tuple[int, int, int],
    **a: Any,
) -> Any:
    inputs: dict[str, tuple[int, Any]] = {
        "ref": (11, REFNUM),
        "info": (9, CLUSTER),
    }
    if "retrieve" in a:
        inputs["retrieve"] = (10, BOOL)
    return _run(
        tmp_path,
        monkeypatch,
        "Enum Registry Values Simple",
        [
            (0, "output", ERROR),
            (1, "output", ARRAY),
            (2, "output", ARRAY),
            (3, "output", REFNUM),
            (4, "output", ARRAY),
            (5, "output", ARRAY),
            (8, "input", ERROR),
            (9, "input", CLUSTER),
            (10, "input", BOOL),
            (11, "input", REFNUM),
        ],
        inputs,
        {
            "kinds": (1, ARRAY),
            "names": (2, ARRAY),
            "text": (4, ARRAY),
            "numbers": (5, ARRAY),
            "error_out": (0, ERROR),
        },
        ref=ref,
        info=value_info,
        **a,
    )


class FakeKey:
    """A ``winreg`` key handle of the fake registry."""

    def __init__(self, registry: FakeRegistry, path: str) -> None:
        self.registry = registry
        self.path = path
        self.closed = False

    def Close(self) -> None:  # noqa: N802
        self.closed = True

    def __int__(self) -> int:
        return 4096


def _os_error(winerror: int, text: str) -> OSError:
    error = OSError(2, text)
    error.winerror = winerror  # type: ignore[attr-defined]
    return error


class FakeRegistry:
    """The ``winreg`` functions and constants the catalog's Python uses, over a
    dict: ``values`` is each key path's ``{name: (data, type)}``."""

    HKEY_CLASSES_ROOT = HKEY_CLASSES_ROOT
    HKEY_CURRENT_USER = HKEY_CURRENT_USER
    HKEY_LOCAL_MACHINE = HKEY_LOCAL_MACHINE
    HKEY_USERS = 0x80000003
    HKEY_PERFORMANCE_DATA = 0x80000004
    HKEY_CURRENT_CONFIG = 0x80000005
    HKEY_DYN_DATA = 0x80000006
    KEY_QUERY_VALUE = 1
    KEY_SET_VALUE = 2
    KEY_CREATE_SUB_KEY = 4
    KEY_ENUMERATE_SUB_KEYS = 8
    KEY_NOTIFY = 16
    KEY_CREATE_LINK = 32
    KEY_READ = 0x20019
    KEY_WRITE = 0x20006
    KEY_ALL_ACCESS = 0xF003F
    KEY_WOW64_32KEY = 0x200
    KEY_WOW64_64KEY = 0x100
    REG_NONE = 0
    REG_SZ = 1
    REG_EXPAND_SZ = 2
    REG_BINARY = 3
    REG_DWORD = 4
    REG_MULTI_SZ = 7
    REG_QWORD = 11

    def __init__(self) -> None:
        self.values: dict[str, dict[str, tuple[Any, int]]] = {}
        self.opened: list[tuple[Any, str, int, int]] = []
        self.connected: list[tuple[str, int]] = []
        self.fail_open: OSError | None = None

    def module(self) -> types.ModuleType:
        module = types.ModuleType("winreg")
        for name in dir(self):
            if name.isupper():
                setattr(module, name, getattr(self, name))
        module.OpenKeyEx = self.OpenKeyEx  # type: ignore[attr-defined]
        module.ConnectRegistry = self.ConnectRegistry  # type: ignore[attr-defined]
        module.EnumValue = self.EnumValue  # type: ignore[attr-defined]
        module.QueryValueEx = self.QueryValueEx  # type: ignore[attr-defined]
        return module

    def ConnectRegistry(self, machine: str, root: int) -> FakeKey:  # noqa: N802
        self.connected.append((machine, root))
        return FakeKey(self, f"\\\\{machine}")

    def OpenKeyEx(  # noqa: N802
        self, base: Any, subkey: str, reserved: int, access: int
    ) -> FakeKey:
        self.opened.append((base, subkey, reserved, access))
        if self.fail_open is not None:
            raise self.fail_open
        if subkey not in self.values:
            raise _os_error(2, "The system cannot find the file specified")
        return FakeKey(self, subkey)

    def EnumValue(self, key: FakeKey, index: int) -> tuple[str, Any, int]:  # noqa: N802
        items = list(self.values[key.path].items())
        if index >= len(items):
            raise _os_error(259, "No more data is available")
        name, (data, kind) = items[index]
        return name, data, kind

    def QueryValueEx(self, key: FakeKey, name: str) -> tuple[Any, int]:  # noqa: N802
        if name not in self.values[key.path]:
            raise _os_error(2, "The system cannot find the file specified")
        return self.values[key.path][name]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeRegistry:
    registry = FakeRegistry()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", registry.module())
    return registry


def _handle(registry: FakeRegistry, path: str) -> dict[str, Any]:
    return dict(hKey=FakeKey(registry, path), hRemoteKey=None)


# --- a system that is not Windows -------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="the refusal is for other systems")
@pytest.mark.parametrize(
    "vi", ["Open Registry Key", "Close Registry Key", "Query Registry Key Info"]
)
def test_other_platforms_raise_not_implemented_naming_the_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, vi: str
) -> None:
    ref = dict(hKey=None, hRemoteKey=None)
    with pytest.raises(NotImplementedError, match=sys.platform):
        if vi == "Open Registry Key":
            _open(tmp_path, monkeypatch, subkey="Software", root=ROOT_USER)
        elif vi == "Close Registry Key":
            _close(tmp_path, monkeypatch, ref)
        else:
            _query(tmp_path, monkeypatch, ref)


@pytest.mark.skipif(sys.platform == "win32", reason="the refusal is for other systems")
def test_read_and_enum_on_other_platforms_raise_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = dict(hKey=None, hRemoteKey=None)
    with pytest.raises(NotImplementedError, match=sys.platform):
        _read(tmp_path, monkeypatch, ref, name="x", default="")
    with pytest.raises(NotImplementedError, match=sys.platform):
        _enum(tmp_path, monkeypatch, ref, (0, 0, 0))


# --- Open Registry Key -------------------------------------------------------


def test_open_uses_ni_defaults_and_returns_the_key_in_a_refnum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: FakeRegistry
) -> None:
    fake.values["Software\\x"] = {}
    result = _open(tmp_path, monkeypatch, subkey="Software\\x", root=ROOT_USER)
    assert fake.opened == [
        (HKEY_CURRENT_USER, "Software\\x", 0, FakeRegistry.KEY_READ | 0x20006)
    ]
    assert result.error_out is None or result.error_out.code == 0
    assert result.refnum["hKey"].path == "Software\\x"
    assert result.refnum["hRemoteKey"] is None


@pytest.mark.parametrize(
    ("root", "constant"),
    [
        (0, HKEY_CLASSES_ROOT),
        (1, HKEY_CURRENT_USER),
        (2, HKEY_LOCAL_MACHINE),
        (3, 0x80000003),
        (4, 0x80000004),
        (5, 0x80000005),
        (6, 0x80000006),
    ],
)
def test_open_root_key_enum_is_nis_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeRegistry,
    root: int,
    constant: int,
) -> None:
    fake.values["s"] = {}
    _open(tmp_path, monkeypatch, subkey="s", root=root)
    assert fake.opened[0][0] == constant


@pytest.mark.parametrize(
    ("mask", "expected"),
    [
        (0, 1),
        (1, 2),
        (2, 4),
        (3, 8),
        (4, 16),
        (5, 32),
        (6, 0x20019),
        (7, 0x20006),
        (8, 0xF003F),
        (9, 0x20019 | 0x20006),
    ],
)
def test_open_security_access_mask_enum_is_nis_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeRegistry,
    mask: int,
    expected: int,
) -> None:
    fake.values["s"] = {}
    _open(tmp_path, monkeypatch, subkey="s", root=ROOT_USER, mask=mask)
    assert fake.opened[0][3] == expected


@pytest.mark.parametrize(("view", "flag"), [(0, 0), (8, 0x200), (9, 0x100)])
def test_open_registry_view_adds_the_wow64_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeRegistry,
    view: int,
    flag: int,
) -> None:
    fake.values["s"] = {}
    _open(
        tmp_path, monkeypatch, subkey="s", root=ROOT_USER, mask=MASK_READ, view=view
    )
    assert fake.opened[0][3] == 0x20019 | flag


def test_open_a_registry_view_NI_does_not_list_is_error_600(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: FakeRegistry
) -> None:
    result = _open(tmp_path, monkeypatch, subkey="s", root=ROOT_USER, view=3)
    assert result.error_out.code == -600
    assert "3" in result.error_out.message
    assert fake.opened == []


def test_open_a_machine_connects_to_its_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: FakeRegistry
) -> None:
    fake.values["s"] = {}
    result = _open(
        tmp_path, monkeypatch, subkey="s", root=ROOT_MACHINE, machine="PLANT7"
    )
    assert fake.connected == [("\\\\PLANT7", HKEY_LOCAL_MACHINE)]
    assert result.refnum["hRemoteKey"].path == "\\\\\\\\PLANT7"
    assert fake.opened[0][0].path == "\\\\\\\\PLANT7"


@pytest.mark.parametrize(
    ("winerror", "code"),
    [
        (2, -603),
        (3, -603),
        (5, -604),
        (6, -602),
        (53, -620),
        (259, -605),
        (1722, -619),
        (1009, -606),
        (1011, -608),
        (1021, -618),
        (87, -600),
    ],
)
def test_open_failures_are_the_windows_registry_error_codes_ni_lists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeRegistry,
    winerror: int,
    code: int,
) -> None:
    fake.fail_open = _os_error(winerror, "boom")
    result = _open(tmp_path, monkeypatch, subkey="s", root=ROOT_USER)
    assert result.error_out.code == code
    assert result.error_out.status is True
    assert result.refnum is None


def test_open_a_missing_key_names_it_in_the_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: FakeRegistry
) -> None:
    result = _open(tmp_path, monkeypatch, subkey="Software\\nope", root=ROOT_USER)
    assert result.error_out.code == -603
    assert result.refnum is None


# --- Close Registry Key ------------------------------------------------------


def test_close_closes_the_key_and_the_remote_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: FakeRegistry
) -> None:
    ref = dict(hKey=FakeKey(fake, "k"), hRemoteKey=FakeKey(fake, "remote"))
    result = _close(tmp_path, monkeypatch, ref)
    assert ref["hKey"].closed and ref["hRemoteKey"].closed
    assert result.error_out is None or result.error_out.code == 0


def test_close_of_something_that_is_not_a_key_is_error_602(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: FakeRegistry
) -> None:
    result = _close(tmp_path, monkeypatch, None)
    assert result.error_out.code == -602
    assert "None" in result.error_out.message


# --- Read Registry Value Simple STR -----------------------------------------


def test_read_a_string_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: FakeRegistry
) -> None:
    fake.values["k"] = {"cmd": ("firefox.exe", 1), "": ("(default)", 1)}
    result = _read(
        tmp_path, monkeypatch, _handle(fake, "k"), name="cmd", default="none"
    )
    assert (result.data, result.kind) == ("firefox.exe", 0)
    assert result.error_out is None or result.error_out.code == 0
    default_value = _read(
        tmp_path, monkeypatch, _handle(fake, "k"), name="", default="none"
    )
    assert default_value.data == "(default)"


def test_read_a_missing_value_returns_the_default_data_without_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: FakeRegistry
) -> None:
    fake.values["k"] = {}
    result = _read(
        tmp_path, monkeypatch, _handle(fake, "k"), name="nope", default="fallback"
    )
    assert (result.data, result.kind) == ("fallback", 0)
    assert result.error_out is None or result.error_out.code == 0


def test_read_through_a_refnum_that_is_not_a_key_returns_the_default_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: FakeRegistry
) -> None:
    result = _read(tmp_path, monkeypatch, None, name="x", default="fallback")
    assert result.data == "fallback"


def test_read_a_dword_gives_the_default_data_and_the_dword_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: FakeRegistry
) -> None:
    fake.values["k"] = {"n": (7, 4)}
    result = _read(tmp_path, monkeypatch, _handle(fake, "k"), name="n", default="d")
    assert (result.data, result.kind) == ("d", 2)


def test_read_binary_is_the_stored_bytes_as_a_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: FakeRegistry
) -> None:
    fake.values["k"] = {"b": (b"\x00\xffA", 3), "m": (["a", "b"], 7)}
    result = _read(tmp_path, monkeypatch, _handle(fake, "k"), name="b", default="")
    assert (result.data, result.kind) == ("\x00\xffA", 1)
    multi = _read(tmp_path, monkeypatch, _handle(fake, "k"), name="m", default="")
    assert multi.kind == 1
    assert multi.data.encode("latin-1").decode("utf-16-le") == "a\x00b\x00\x00"


# --- Enum Registry Values Simple ---------------------------------------------


def test_enum_values_without_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: FakeRegistry
) -> None:
    fake.values["k"] = {"a": ("text", 1), "b": (5, 4), "c": (b"\x01", 3)}
    result = _enum(tmp_path, monkeypatch, _handle(fake, "k"), (3, 1, 4))
    assert result.names == ["a", "b", "c"]
    assert result.kinds == [0, 2, 1]
    assert result.text == []
    assert result.numbers == []


def test_enum_values_with_data_has_one_element_per_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: FakeRegistry
) -> None:
    fake.values["k"] = {
        "a": ("text", 1),
        "b": (5, 4),
        "c": (b"\x01", 3),
        "d": ("%PATH%", 2),
    }
    result = _enum(
        tmp_path, monkeypatch, _handle(fake, "k"), (4, 1, 6), retrieve=True
    )
    assert result.kinds == [0, 2, 1, 0]
    assert result.text == ["text", "", "\x01", "%PATH%"]
    assert result.numbers == [0, 5, 0, 0]


def test_enum_more_values_than_the_key_has_is_error_605(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: FakeRegistry
) -> None:
    fake.values["k"] = {"a": ("x", 1)}
    result = _enum(tmp_path, monkeypatch, _handle(fake, "k"), (2, 1, 1))
    assert result.error_out.code == -605


def test_enum_of_something_that_is_not_a_key_is_error_602(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: FakeRegistry
) -> None:
    result = _enum(tmp_path, monkeypatch, None, (0, 0, 0))
    assert result.error_out.code == -602


# --- Query Registry Key Info ---------------------------------------------------


class FakeAdvapi32:
    """advapi32 with ``RegQueryInfoKeyW`` answering from ``answer``; the first
    ``more_data`` calls say the class buffer is too small."""

    answer = dict(
        key_class="Config",
        subkeys=3,
        max_subkey=12,
        max_class=7,
        values=5,
        max_name=20,
        max_data=64,
    )
    more_data = 0
    status = 0
    calls: list[int] = []

    def __init__(self, name: str, **options: Any) -> None:
        self.name = name

    def RegQueryInfoKeyW(self, *args: Any) -> int:  # noqa: N802
        (_key, key_class, class_len, _r, subkeys, max_sub, max_class) = args[:7]
        values, max_name, max_data = args[7:10]
        FakeAdvapi32.calls.append(class_len._obj.value)  # noqa: SLF001
        if FakeAdvapi32.status:
            return FakeAdvapi32.status
        if FakeAdvapi32.more_data:
            FakeAdvapi32.more_data -= 1
            class_len._obj.value = 300  # noqa: SLF001
            return 234
        key_class.value = self.answer["key_class"]
        class_len._obj.value = len(self.answer["key_class"])  # noqa: SLF001
        for out, name in (
            (subkeys, "subkeys"),
            (max_sub, "max_subkey"),
            (max_class, "max_class"),
            (values, "values"),
            (max_name, "max_name"),
            (max_data, "max_data"),
        ):
            out._obj.value = self.answer[name]  # noqa: SLF001
        return 0


@pytest.fixture
def fake_advapi32(monkeypatch: pytest.MonkeyPatch) -> type[FakeAdvapi32]:
    FakeAdvapi32.calls = []
    FakeAdvapi32.more_data = 0
    FakeAdvapi32.status = 0
    monkeypatch.setattr(ctypes, "WinDLL", FakeAdvapi32, raising=False)
    return FakeAdvapi32


def test_query_returns_the_class_and_the_value_and_subkey_info(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeRegistry,
    fake_advapi32: type[FakeAdvapi32],
) -> None:
    result = _query(tmp_path, monkeypatch, _handle(fake, "k"))
    assert result.key_class == "Config"
    assert tuple(result.value_info) == (5, 20, 64)
    assert tuple(result.subkey_info) == (3, 12, 7)


def test_query_grows_the_class_buffer_when_windows_says_it_is_too_small(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeRegistry,
    fake_advapi32: type[FakeAdvapi32],
) -> None:
    fake_advapi32.more_data = 1
    result = _query(tmp_path, monkeypatch, _handle(fake, "k"))
    assert fake_advapi32.calls == [256, 301]
    assert result.key_class == "Config"


def test_query_failure_is_the_registry_error_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeRegistry,
    fake_advapi32: type[FakeAdvapi32],
) -> None:
    fake_advapi32.status = 6
    result = _query(tmp_path, monkeypatch, _handle(fake, "k"))
    assert result.error_out.code == -602


# --- the neighbours that stay a gap --------------------------------------------


def test_create_registry_key_stays_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError, match="Create Registry Key.vi"):
        run_catalog_call(
            tmp_path,
            monkeypatch,
            [*LLB, "Create Registry Key.vi"],
            pane=[(0, "output", ERROR), (3, "output", REFNUM)],
            inputs={},
            outputs={"refnum": (3, REFNUM)},
            arguments={},
            gaps=gaps,
            runtime_not_implemented=True,
        )
    assert [gap.kind for gap in gaps] == ["terminal_mapping"]


# --- the real registry, on Windows ---------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="the real registry is Windows")
def test_real_registry_open_read_enum_query_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import winreg

    subkey = f"Software\\lvkit-test\\{uuid.uuid4().hex}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, subkey) as made:
        winreg.SetValueEx(made, "text", 0, winreg.REG_SZ, "hello")
        winreg.SetValueEx(made, "number", 0, winreg.REG_DWORD, 42)
        winreg.SetValueEx(made, "blob", 0, winreg.REG_BINARY, b"\x01\x02\xff")
        winreg.SetValueEx(made, "multi", 0, winreg.REG_MULTI_SZ, ["a", "b"])
        winreg.CreateKey(made, "child").Close()
    try:
        opened = _open(tmp_path, monkeypatch, subkey=subkey, root=ROOT_USER)
        assert opened.error_out is None or opened.error_out.code == 0
        ref = opened.refnum
        text = _read(tmp_path, monkeypatch, ref, name="text", default="d")
        assert (text.data, text.kind) == ("hello", 0)
        number = _read(tmp_path, monkeypatch, ref, name="number", default="d")
        assert (number.data, number.kind) == ("d", 2)
        blob = _read(tmp_path, monkeypatch, ref, name="blob", default="d")
        assert (blob.data, blob.kind) == ("\x01\x02\xff", 1)
        missing = _read(tmp_path, monkeypatch, ref, name="nope", default="d")
        assert missing.data == "d"
        info = _query(tmp_path, monkeypatch, ref)
        assert info.value_info[0] == 4
        assert info.subkey_info[0] == 1
        assert info.subkey_info[1] == len("child")
        assert info.value_info[1] == len("number")
        listing = _enum(
            tmp_path, monkeypatch, ref, tuple(info.value_info), retrieve=True
        )
        by_name = dict(zip(listing.names, range(len(listing.names)), strict=True))
        assert sorted(by_name) == ["blob", "multi", "number", "text"]
        assert listing.kinds[by_name["text"]] == 0
        assert listing.kinds[by_name["number"]] == 2
        assert listing.kinds[by_name["blob"]] == 1
        assert listing.kinds[by_name["multi"]] == 1
        assert listing.text[by_name["text"]] == "hello"
        assert listing.numbers[by_name["number"]] == 42
        assert listing.text[by_name["blob"]] == "\x01\x02\xff"
        past = _enum(tmp_path, monkeypatch, ref, (5, 1, 1))
        assert past.error_out.code == -605
        closed = _close(tmp_path, monkeypatch, ref)
        assert closed.error_out is None or closed.error_out.code == 0
        with pytest.raises(OSError):
            winreg.QueryValueEx(ref["hKey"], "text")
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, f"{subkey}\\child")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, "Software\\lvkit-test")
        except OSError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="the real registry is Windows")
def test_real_registry_open_a_missing_key_is_error_603(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _open(
        tmp_path,
        monkeypatch,
        subkey=f"Software\\lvkit-test\\{uuid.uuid4().hex}",
        root=ROOT_USER,
    )
    assert result.error_out.code == -603
    assert result.refnum is None


@pytest.mark.skipif(sys.platform != "win32", reason="the real registry is Windows")
def test_real_registry_write_without_write_access_is_error_604(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _open(
        tmp_path,
        monkeypatch,
        subkey="SOFTWARE\\Microsoft\\Windows\\CurrentVersion",
        root=ROOT_MACHINE,
        mask=MASK_READ_WRITE,
    )
    if result.error_out is not None and result.error_out.code != 0:
        assert result.error_out.code == -604
    else:
        pytest.skip("the test user can write HKEY_LOCAL_MACHINE")
