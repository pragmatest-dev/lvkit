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
import inspect
import os
import shutil
import subprocess
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

from lvkit import cli, pipeline
from lvkit.codegen.nodes.ext_func_library import LIBRARY_FILE_SOURCE, expansions
from lvkit.graph import InMemoryVIGraph
from lvkit.parser.extfunc_prototype import (
    ExtFuncParam,
    LibraryPathKind,
    ParamKind,
)
from lvkit.pipeline import generate_python
from lvkit.run_report import Problem, ProblemKind, ProblemStage, RunStopped
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


def _loader(name: str, load: str) -> str:
    return f"@functools.cache\ndef {name}():\n    return {load}\n"


_ABS = (
    "@functools.cache\ndef {n}_abs():\n"
    "    function = {n}()['abs']\n"
    "    function.argtypes = [ctypes.c_int32]\n"
    "    function.restype = ctypes.c_int32\n"
    "    return function\n"
)


def test_a_stdcall_library_loads_with_windll_where_windows_has_it() -> None:
    source = _text("kernel32.dll", "stdcall")
    assert (
        _loader(
            "_kernel32_dll", "getattr(ctypes, 'WinDLL', ctypes.CDLL)('kernel32.dll')"
        )
        + "\n"
        + _ABS.format(n="_kernel32_dll")
    ) in source
    assert "import sys" not in source
    assert "lvkit" not in source


def test_a_c_library_loads_with_cdll() -> None:
    source = _text("libfoo.so", "c")
    assert (
        _loader("_libfoo_so", "ctypes.CDLL('libfoo.so')")
        + "\n"
        + _ABS.format(n="_libfoo_so")
        in source
    )


def test_a_wildcard_name_is_expanded_when_the_library_is_first_loaded() -> None:
    source = _text("nilvaiu.*", "c")
    assert ast.unparse(ast.parse(LIBRARY_FILE_SOURCE)) in source
    assert _loader("_nilvaiu_", "ctypes.CDLL(_library_file('nilvaiu.*'))") in source
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


def _import(out: Path, package: str = "vis.sub") -> types.ModuleType:
    sys.path.insert(0, str(out.parent))
    try:
        return importlib.import_module(f"clfn_bdhb.{package}.clfn_bdhbxml")
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
        "def _vendorlib_():\n"
        "    return getattr(ctypes, 'WinDLL', ctypes.CDLL)(os.path.join("
        "os.path.dirname(__file__), '..', 'libs', _library_file('vendorlib**.*')))\n"
    ) in source
    assert "lvkit" not in source
    module = _import(out)
    loaded = Path(module._vendorlib_()._name).resolve()  # noqa: SLF001
    assert loaded == (out / "vis/libs/vendorlib_64.so").resolve()
    pair = (module.ctypes.c_uint16 * 2)(1, 2)
    module._vendorlib_().swap(pair)  # noqa: SLF001
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
    assert module._liba_so().a_value() == 42  # noqa: SLF001


@pytest.mark.skipif(sys.platform == "win32", reason="the library is a Linux .so")
def test_a_library_sits_in_the_package_folder_of_the_modules_beside_it(
    project: Project,
    library: str,  # noqa: F811
) -> None:
    """The folder ``Vendor Libs`` is the package ``vendor_libs`` for the VI module
    and for the library; the library's own file name is not changed."""
    folder = project.root / "Vendor Libs"
    folder.mkdir()
    project.vi_folder = project.libs = folder
    shutil.copy(library, folder / "Vendor Lib_64.so")
    vi = project.write_vi("Vendor Lib**.*", ["", "Vendor Lib.dll"], "swap")

    out = project.generate(vi)

    assert _tree(out) == [
        "__init__.py",
        "errors.py",
        "panels.py",
        "vendor_libs/Vendor Lib_64.so",
        "vendor_libs/__init__.py",
        "vendor_libs/clfn_bdhbxml.py",
    ]
    source = (out / "vendor_libs/clfn_bdhbxml.py").read_text()
    assert (
        "os.path.join(os.path.dirname(__file__), _library_file('Vendor Lib**.*'))"
    ) in source
    module = _import(out, "vendor_libs")
    loaded = Path(module._vendor_lib_()._name).resolve()  # noqa: SLF001
    assert loaded == (out / "vendor_libs/Vendor Lib_64.so").resolve()
    pair = (module.ctypes.c_uint16 * 2)(3, 4)
    module._vendor_lib_().swap(pair)  # noqa: SLF001
    assert list(pair) == [4, 3]


def test_a_project_library_that_is_not_on_disk_is_a_gap(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    vi = project.write_vi("vendorlib.*", ["", "..", "libs", "vendorlib.dll"], "swap")
    out = project.generate(vi)
    assert not list(out.rglob("*.error.py"))
    source = (out / "vis/sub/clfn_bdhbxml.py").read_text()
    reason = "no file vendorlib.dll or vendorlib.so or vendorlib.framework is in"
    assert reason in source
    assert "raise NotImplementedError(" in source
    # The gap leaves no library load or prototype behind.
    assert "CDLL" not in source and "argtypes" not in source
    (gap,) = project.result["gaps"]
    assert gap.kind == "unsupported_node"
    assert "'vendorlib.*' at the path '../libs/vendorlib.dll'" in gap.reason
    assert capsys.readouterr().err.startswith("warning: ")
    module = _import(out)
    (function,) = [
        f
        for f in vars(module).values()
        if inspect.isfunction(f) and f.__module__ == module.__name__
    ]
    with pytest.raises(NotImplementedError, match="vendorlib.dll or vendorlib.so"):
        function()


def test_a_library_beside_a_user_lib_vi_is_copied_beside_its_module(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A VI in a user.lib folder is placed under ``userlib``; the library it names
    relative to itself is copied by the same moves from its module, so the
    module's path to it holds in the package."""
    home = project.base.resolve()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    userlib = project.base / "user.lib"
    project.vi_folder = userlib / "Vendor" / "sub"
    project.libs = userlib / "Vendor" / "libs"
    project.vi_folder.mkdir(parents=True)
    project.libs.mkdir(parents=True)
    (project.libs / "vendorlib.so").write_bytes(b"not a library")
    vi = project.write_vi("vendorlib.so", ["", "..", "libs", "vendorlib.so"], "f")

    project.result = generate_python(vi, project.base / "out", userlib_root=userlib)
    out = Path(project.result["output_dir"])

    assert "userlib/vendor/libs/vendorlib.so" in _tree(out)
    source = (out / "userlib/vendor/sub/clfn_bdhbxml.py").read_text()
    assert (
        "os.path.join(os.path.dirname(__file__), '..', 'libs', 'vendorlib.so')"
    ) in source
    target = (out / "userlib/vendor/sub" / ".." / "libs" / "vendorlib.so").resolve()
    assert target.is_file()


def test_a_project_copy_placed_by_its_userlib_path_takes_its_library_along(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A VI kept in the project but recorded by its callers as ``<userlib>/...``
    is generated under ``userlib``; the library beside it goes there too, so the
    module's path to it holds in the package."""
    (project.libs / "vendorlib.so").write_bytes(b"not a library")
    vi = project.write_vi("vendorlib.so", ["", "..", "libs", "vendorlib.so"], "f")
    recorded = "<userlib>/Vendor/sub/Clfn_BDHb.xml"
    key = str(vi.resolve())
    original = InMemoryVIGraph.recorded_path

    def recorded_path(graph: InMemoryVIGraph, asked: str) -> str | None:
        return recorded if asked == key else original(graph, asked)

    monkeypatch.setattr(InMemoryVIGraph, "recorded_path", recorded_path)

    out = project.generate(vi)

    assert "userlib/vendor/libs/vendorlib.so" in _tree(out)
    assert not [p for p in _tree(out) if p.startswith("vis/")]
    module = out / "userlib/vendor/sub/clfn_bdhbxml.py"
    assert (
        "os.path.join(os.path.dirname(__file__), '..', 'libs', 'vendorlib.so')"
    ) in module.read_text()
    assert (module.parent / ".." / "libs" / "vendorlib.so").resolve().is_file()


def test_a_relative_path_through_a_folder_and_back_finds_the_library(
    project: Project,
) -> None:
    (project.libs / "vendorlib.so").write_bytes(b"not a library")
    tokens = ["", "nothere", "..", "..", "libs", "vendorlib.so"]
    vi = project.write_vi("vendorlib.so", tokens, "f")
    out = project.generate(vi)
    assert "vis/libs/vendorlib.so" in _tree(out)
    source = (out / "vis/sub/clfn_bdhbxml.py").read_text()
    assert "os.path.dirname(__file__), '..', 'libs', 'vendorlib.so')" in source


@pytest.mark.skipif(
    sys.platform in ("win32", "darwin"), reason="needs names that differ only in case"
)
def test_two_files_for_one_place_stop_generation_with_a_whole_package(
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``vendorlib.so`` (the typed name's expansion) and ``VendorLib.so`` (the
    recorded name) are one place on a Windows or macOS file system: generation
    stops as unwritable (exit 1), and the package it wrote is whole."""
    (project.libs / "vendorlib.so").write_bytes(b"one")
    (project.libs / "VendorLib.so").write_bytes(b"two")
    vi = project.write_vi("vendorlib.*", ["", "..", "libs", "VendorLib.so"], "f")
    out = project.base / "out"
    monkeypatch.setattr(sys, "argv", ["lvkit", "generate", str(vi), "-o", str(out)])

    assert cli.main() == 1

    err = capsys.readouterr().err
    assert "'vis/libs/VendorLib.so'" in err and "'vis/libs/vendorlib.so'" in err
    (package,) = [p for p in out.iterdir() if p.is_dir()]
    assert (package / "__init__.py").is_file()
    assert (package / "vis/sub/clfn_bdhbxml.py").is_file()
    assert (package / "vis/libs/vendorlib.so").read_bytes() == b"one"
    _import(package)  # every module written imports


def test_a_stopped_run_still_copies_the_libraries_of_the_modules_it_wrote(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fail-fast run that stops after a module is written (its gaps reported)
    leaves the library that module loads beside it."""
    (project.libs / "vendorlib.so").write_bytes(b"not a library")
    vi = project.write_vi("vendorlib.so", ["", "..", "libs", "vendorlib.so"], "f")
    stop = RunStopped(
        Problem(
            vi="vis/sub/Clfn.vi",
            stage=ProblemStage.GENERATE,
            kind=ProblemKind.GAP,
            reason="a gap",
        )
    )

    def report_gaps(*args: object) -> None:
        raise stop

    monkeypatch.setattr(pipeline, "_report_gaps", report_gaps)

    out = project.generate(vi)

    assert "vis/sub/clfn_bdhbxml.py" in _tree(out)
    assert "vis/libs/vendorlib.so" in _tree(out)


def test_a_module_whose_data_files_fail_is_not_written(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The data files go first: when they cannot be written, no module is left
    that would read them, and its library is not copied."""
    (project.libs / "vendorlib.so").write_bytes(b"not a library")
    vi = project.write_vi("vendorlib.so", ["", "..", "libs", "vendorlib.so"], "f")

    def unwritable(*args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(pipeline, "_write_data_files", unwritable)

    out = project.generate(vi)

    assert "vis/sub/clfn_bdhbxml.py" not in _tree(out)
    assert "vis/libs/vendorlib.so" not in _tree(out)


def test_a_library_outside_the_project_is_loaded_by_name(project: Project) -> None:
    (project.base / "outside.so").write_bytes(b"x")
    vi = project.write_vi("outside.so", ["", "..", "..", "..", "..", "outside.so"], "f")
    out = project.generate(vi)
    assert not [p for p in _tree(out) if p.endswith(".so")]
    source = (out / "vis/sub/clfn_bdhbxml.py").read_text()
    assert (
        _loader("_outside_so", "getattr(ctypes, 'WinDLL', ctypes.CDLL)('outside.so')")
        in source
    )


def test_the_recorded_kind_decides_whether_the_path_is_used() -> None:
    source = _text(
        "kernel32.dll",
        "stdcall",
        library_path_tokens=("", "..", "kernel32.dll"),
        library_path_kind=LibraryPathKind.OTHER,
    )
    assert "('kernel32.dll')" in source
