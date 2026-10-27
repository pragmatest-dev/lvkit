"""How a Call Library Function Node's library is loaded: the loader for the
calling convention, wildcard names, a wired ``path in``, and a library inside the
project that is copied into the generated package with the libraries it needs.

Text cases build a one-node VI and assert the module's exact lines. The project
cases write a tiny project folder (a VI whose ``NEXF`` record gives the library's
path from the VI), generate the package with ``generate_python``, assert the
copied files and import the package, so the library is loaded from its copy."""

from __future__ import annotations

import ast
import importlib
import os
import shutil
import subprocess
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

from lvkit.codegen.nodes.ext_func_library import LIBRARY_FILE_SOURCE, expansions
from lvkit.parser.extfunc_prototype import (
    ExtFuncParam,
    LibraryPathKind,
    ParamKind,
)
from lvkit.pipeline import generate_python
from tests.ext_func_vi import build_vi
from tests.test_ext_func_c_layouts import library  # noqa: F401
from tests.test_ext_func_terminals import _BD_XML, _MAIN_XML
from tests.test_extfunc_links import _record

_NUMBER = ExtFuncParam(ParamKind.VALUE, "int32")


def _text(library_name: str, convention: str, **fields: object) -> str:
    _, source = build_vi(
        library_name,
        "abs",
        [_NUMBER, _NUMBER],
        ["NumInt32", "NumInt32"],
        controls={1: "x"},
        indicators={0: "result"},
        convention=convention,
        call_fields=dict(fields),
        load=False,
    )
    return source


_ABS = (
    "{n}_abs = {n}['abs']\n{n}_abs.argtypes = [ctypes.c_int32]\n"
    "{n}_abs.restype = ctypes.c_int32\n"
)


def test_a_stdcall_library_loads_with_windll_where_windows_has_it() -> None:
    source = _text("kernel32.dll", "stdcall")
    assert (
        "_kernel32_dll = getattr(ctypes, 'WinDLL', ctypes.CDLL)('kernel32.dll')\n"
        + _ABS.format(n="_kernel32_dll")
    ) in source
    assert "import sys" not in source
    assert "lvkit" not in source


def test_a_c_library_loads_with_cdll() -> None:
    source = _text("libfoo.so", "c")
    assert "_libfoo_so = ctypes.CDLL('libfoo.so')\n" + _ABS.format(n="_libfoo_so") in (
        source
    )


def test_a_wildcard_name_is_expanded_when_the_module_is_imported() -> None:
    source = _text("nilvaiu.*", "c")
    assert ast.unparse(ast.parse(LIBRARY_FILE_SOURCE)) in source
    assert "_nilvaiu_ = ctypes.CDLL(_library_file('nilvaiu.*'))\n" in source
    assert source.count("def _library_file") == 1
    assert "import os\nimport sys\n" in source


def test_a_wired_path_has_its_file_name_expanded_when_the_node_runs() -> None:
    _, source = build_vi(
        "ignored",
        "abs",
        [_NUMBER, _NUMBER],
        ["NumInt32", "NumInt32"],
        controls={1: "x"},
        indicators={0: "result"},
        path_control="lib",
        load=False,
    )
    assert (
        "    _abs_library = ctypes.CDLL(_library_file(lib))\n"
        "    _abs_call = _abs_library['abs']\n"
    ) in source
    assert "def _library_file(name):" in source


def test_a_wired_stdcall_path_loads_with_windll_where_windows_has_it() -> None:
    _, source = build_vi(
        None,
        "abs",
        [_NUMBER, _NUMBER],
        ["NumInt32", "NumInt32"],
        controls={1: "x"},
        indicators={0: "result"},
        convention="stdcall",
        path_control="lib",
        load=False,
    )
    assert (
        "_abs_library = getattr(ctypes, 'WinDLL', ctypes.CDLL)(_library_file(lib))"
        in source
    )


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
@pytest.mark.parametrize("bits", [32, 64])
@pytest.mark.parametrize(
    "name,stem",
    [
        ("myshared.*", "myshared"),
        ("myshared*.*", "myshared{bits}"),
        ("my*shared.*", "my{bits}shared"),
        ("myshared**.*", "myshared{wide}"),
        ("my**shared.*", "my{wide}shared"),
    ],
)
def test_wildcards_expand_as_ni_documents(
    platform: str, bits: int, name: str, stem: str
) -> None:
    fake_sys = types.SimpleNamespace(
        platform=platform, maxsize=2**63 - 1 if bits == 64 else 2**31 - 1
    )
    namespace: dict[str, object] = {"os": os, "sys": fake_sys}
    exec(LIBRARY_FILE_SOURCE, namespace)  # noqa: S102
    stem = stem.format(bits=bits, wide="_64" if bits == 64 else "")
    suffix = {
        "win32": ".dll",
        "linux": ".so",
        "darwin": f".framework/{stem}",
    }[platform]
    expand = namespace["_library_file"]
    assert expand(name) == stem + suffix  # type: ignore[operator]
    assert expand(f"libs/{name}") == os.path.join(  # type: ignore[operator]
        "libs", stem + suffix
    )


def test_a_name_without_a_wildcard_is_left_alone() -> None:
    namespace: dict[str, object] = {"os": os, "sys": sys}
    exec(LIBRARY_FILE_SOURCE, namespace)  # noqa: S102
    assert namespace["_library_file"]("kernel32.dll") == "kernel32.dll"  # type: ignore[operator]
    assert namespace["_library_file"]("a/b.dll") == os.path.join(  # type: ignore[operator]
        "a", "b.dll"
    )


def test_every_expansion_of_a_wildcard_name_is_listed() -> None:
    assert expansions("x**.*") == [
        "x.dll",
        "x.so",
        "x.framework",
        "x_64.dll",
        "x_64.so",
        "x_64.framework",
    ]
    assert expansions("x.dll") == ["x.dll"]


# ----------------------------------------------------------- a library in the project

_C_SOURCE_A = "int b_value(void); int a_value(void) { return b_value() + 1; }\n"
_C_SOURCE_B = "int b_value(void) { return 41; }\n"


def _compiler() -> str:
    compiler = shutil.which("cc") or shutil.which("gcc")
    if compiler is None:
        pytest.skip("no C compiler")
    return compiler


class Project:
    """A project folder (``vis/sub/Clfn.vi`` and ``vis/libs/``) with one VI whose
    node calls ``function`` in the library its ``NEXF`` record gives."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.root = base / "proj"
        self.vi_folder = self.root / "vis" / "sub"
        self.libs = self.root / "vis" / "libs"
        self.libs.mkdir(parents=True)
        self.vi_folder.mkdir(parents=True)
        (self.root / "p.lvproj").write_text("")
        self.result: dict | None = None

    def write_vi(self, typed: str, tokens: list[str], function: str) -> Path:
        main = _MAIN_XML.format(return_label="")
        main = main.replace(
            '<TypeDesc Type="Array" Format="inline" Label="name">\n'
            '        <Dimension Flags="0x00FF" FixedSize="0xFFFFFF" />\n'
            '        <TypeDesc TypeID="9" />\n      </TypeDesc>',
            '<TypeDesc Type="String" Format="inline" Prop1="0xFFFFFFFF" '
            'Label="name" />',
        ).replace(
            '        <TypeDesc TypeID="2" />\n      </TypeDesc>', "      </TypeDesc>"
        )
        record = _record("NEXF", typed, tokens, function, [20])
        main = main.replace(
            "</LVSR>",
            f"<LIbd><Section><BDHP>{record}</BDHP></Section></LIbd></LVSR>",
        )
        bd = _BD_XML.replace('"DoWork"', f'"{function}"')
        first = bd.index('          <SL__arrayElement class="term" uid="36">')
        last = bd.index("        </termList>\n        <nodeName>")
        bd = bd[:first] + bd[last:]
        bd = bd.replace('<termList elements="10">', '<termList elements="8">')
        nodes = bd.index('    <nodeList elements="3">')
        extfunc = bd.index('      <SL__arrayElement class="extFunc"')
        bd = bd[:nodes] + '    <nodeList elements="1">\n' + bd[extfunc:]
        signals = bd.index("    <signalList")
        bd = (
            bd[:signals]
            + '    <signalList elements="0" />\n  </root>\n</SL__rootObject>\n'
        )
        (self.vi_folder / "Clfn_BDHb.xml").write_text(bd)
        path = self.vi_folder / "Clfn.xml"
        path.write_text(main)
        return self.vi_folder / "Clfn_BDHb.xml"

    def generate(self, vi: Path) -> Path:
        self.result = generate_python(vi, self.base / "out")
        return Path(self.result["output_dir"])


@pytest.fixture
def project(tmp_path: Path) -> Iterator[Project]:
    yield Project(tmp_path)
    for name in [m for m in sys.modules if m.split(".")[0] == "clfn_bdhb"]:
        del sys.modules[name]


def _import(out: Path) -> types.ModuleType:
    sys.path.insert(0, str(out.parent))
    try:
        return importlib.import_module("clfn_bdhb.vis.sub.clfn_bdhbxml")
    finally:
        sys.path.remove(str(out.parent))


def _tree(root: Path) -> list[str]:
    return sorted(
        p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
    )


@pytest.mark.skipif(sys.platform == "win32", reason="the library is a Linux .so")
def test_a_project_library_is_copied_and_loaded_from_its_copy(
    project: Project,
    library: str,  # noqa: F811
) -> None:
    shutil.copy(library, project.libs / "vendorlib_64.so")
    for name in ("vendorlib.dll", "vendorlib_64.dll", "vendorlib.so"):
        (project.libs / name).write_bytes(b"not a library")
    (project.libs / "other.so").write_bytes(b"unrelated")
    bundle = project.libs / "vendorlib_64.framework"
    (bundle / "Resources").mkdir(parents=True)
    (bundle / "vendorlib_64").write_bytes(b"not a library")
    (bundle / "Resources" / "Info.plist").write_bytes(b"<plist/>")
    vi = project.write_vi("vendorlib**.*", ["", "..", "libs", "vendorlib.dll"], "swap")

    out = project.generate(vi)

    assert [p for p in _tree(out) if p.startswith("vis/libs/")] == [
        "vis/libs/vendorlib.dll",
        "vis/libs/vendorlib.so",
        "vis/libs/vendorlib_64.dll",
        "vis/libs/vendorlib_64.framework/Resources/Info.plist",
        "vis/libs/vendorlib_64.framework/vendorlib_64",
        "vis/libs/vendorlib_64.so",
    ]
    source = (out / "vis/sub/clfn_bdhbxml.py").read_text()
    assert (
        "_vendorlib_ = getattr(ctypes, 'WinDLL', ctypes.CDLL)(os.path.join("
        "os.path.dirname(__file__), '..', 'libs', _library_file('vendorlib**.*')))\n"
    ) in source
    assert "lvkit" not in source
    module = _import(out)
    loaded = Path(module._vendorlib_._name).resolve()  # noqa: SLF001
    assert loaded == (out / "vis/libs/vendorlib_64.so").resolve()
    pair = (module.ctypes.c_uint16 * 2)(1, 2)
    module._vendorlib_.swap(pair)  # noqa: SLF001
    assert list(pair) == [2, 1]


@pytest.mark.skipif(sys.platform == "win32", reason="the libraries are Linux .so")
def test_a_library_it_needs_in_the_project_is_copied_too(project: Project) -> None:
    compiler = _compiler()
    for name, text in (("b", _C_SOURCE_B), ("a", _C_SOURCE_A)):
        (project.libs / f"{name}.c").write_text(text)
    subprocess.run(
        [compiler, "-shared", "-fPIC", "-o", str(project.libs / "libb.so"),
         str(project.libs / "b.c")],
        check=True,
    )  # fmt: skip
    subprocess.run(
        [compiler, "-shared", "-fPIC", "-o", str(project.libs / "liba.so"),
         str(project.libs / "a.c"), f"-L{project.libs}", "-lb",
         "-Wl,-rpath,$ORIGIN"],
        check=True,
    )  # fmt: skip
    (project.libs / "libc_unneeded.so").write_bytes(b"x")
    (project.libs / "a.c").unlink()
    (project.libs / "b.c").unlink()
    vi = project.write_vi("liba.so", ["", "..", "libs", "liba.so"], "a_value")

    out = project.generate(vi)

    assert [p for p in _tree(out) if p.startswith("vis/libs/")] == [
        "vis/libs/liba.so",
        "vis/libs/libb.so",
    ]
    module = _import(out)
    assert module._liba_so.a_value() == 42  # noqa: SLF001


def test_a_project_library_that_is_not_on_disk_stops_generation(
    project: Project,
) -> None:
    vi = project.write_vi("vendorlib.*", ["", "..", "libs", "vendorlib.dll"], "swap")
    out = project.generate(vi)
    (error,) = out.rglob("*.error.py")
    message = error.read_text()
    assert "Clfn_BDHb.xml" in message
    assert "'swap'" in message
    assert "'vendorlib.*' at the path '../libs/vendorlib.dll'" in message
    assert "no file vendorlib.dll or vendorlib.so or vendorlib.framework" in message


def test_a_library_outside_the_project_is_loaded_by_name(project: Project) -> None:
    (project.base / "outside.so").write_bytes(b"x")
    vi = project.write_vi("outside.so", ["", "..", "..", "..", "..", "outside.so"], "f")
    out = project.generate(vi)
    assert not [p for p in _tree(out) if p.endswith(".so")]
    source = (out / "vis/sub/clfn_bdhbxml.py").read_text()
    assert (
        "_outside_so = getattr(ctypes, 'WinDLL', ctypes.CDLL)('outside.so')" in source
    )


def test_the_recorded_kind_decides_whether_the_path_is_used() -> None:
    source = _text(
        "kernel32.dll",
        "stdcall",
        library_path_tokens=("", "..", "kernel32.dll"),
        library_path_kind=LibraryPathKind.OTHER,
    )
    assert "('kernel32.dll')" in source
