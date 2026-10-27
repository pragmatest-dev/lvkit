"""How a generated module loads the library a Call Library Function Node names.

A library is loaded by ``ctypes.WinDLL`` (a ``stdcall`` node, on Windows) or
``ctypes.CDLL`` (a C node; a ``stdcall`` node elsewhere), as LabVIEW does: a
library typed in the node's setup is resolved when the module is imported, a
library wired to ``path in`` when the node runs.

The typed name may hold wildcards (NI LabVIEW User Manual, "Configuring the Call
Library Function Node", docs-be.ni.com/bundle/labview, page 2113332):
``.*`` is the platform's library extension (``.dll``, ``.so``, ``.framework``),
``*`` is ``32`` or ``64`` by the bitness of the running process, ``**`` is nothing
on a 32-bit process and ``_64`` on a 64-bit one. ``_library_file`` expands them in
the generated module.

A library with no folder in its recorded path, or a path rooted in a LabVIEW
folder, is loaded by its name: the operating system searches for it. A library
recorded relative to the VI and inside the project is copied into the generated
package at the same project-relative path (with every file another expansion of a
wildcard name selects, and the libraries it needs that sit in its folder), and the
module loads it by its path from the module's own folder.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.parser.extfunc_prototype import ExtFuncCall, LibraryPathKind

from ..context import CodeGenContext
from ..ctypes_module import CtypesModule
from ..library_dependencies import LibraryFormat, library_format, needed_in

# The file names a wildcard name can expand to: the extensions and bit counts.
_EXTENSIONS = (".dll", ".so", ".framework")
_BITS = (32, 64)

LIBRARY_FILE_SOURCE = """\
def _library_file(name):
    folder, base = os.path.split(os.fspath(name))
    if "*" in base:
        bits = 64 if sys.maxsize > 2**32 else 32
        stem = base[:-2] if base.endswith(".*") else base
        stem = stem.replace("**", "_64" if bits == 64 else "").replace("*", str(bits))
        if base.endswith(".*"):
            if sys.platform == "win32":
                base = stem + ".dll"
            elif sys.platform == "darwin":
                base = stem + ".framework/" + stem
            else:
                base = stem + ".so"
        else:
            base = stem
    return os.path.join(folder, base)
"""

_LIBRARY_FILE_NAME = "_library_file"


def expansions(name: str) -> list[str]:
    """Every file or folder name the typed ``name`` can select on some platform
    and bitness; the name itself when it has no wildcard."""
    if "*" not in name:
        return [name]
    extension = name.endswith(".*")
    stem = name[:-2] if extension else name
    found: list[str] = []
    for bits in _BITS:
        expanded = stem.replace("**", "_64" if bits == 64 else "").replace(
            "*", str(bits)
        )
        for suffix in _EXTENSIONS if extension else ("",):
            if expanded + suffix not in found:
                found.append(expanded + suffix)
    return found


@dataclass(frozen=True)
class LibraryLocation:
    """Where the library a node recorded sits: the folder it is looked for in and
    the typed file name."""

    folder: Path
    name: str


def _fail(ctx: CodeGenContext, node: PrimitiveNode, call: ExtFuncCall, why: str):
    tokens = call.library_path_tokens
    empties = next((i for i, t in enumerate(tokens) if t), len(tokens))
    up = [".."] * (empties - 1)
    where = posixpath.normpath("/".join([*(up or ["."]), *tokens[empties:]]))
    return ConversionGap(
        f"{ctx.vi_name}: the Call Library Function Node {call.function!r} records "
        f"the library {call.library!r} at the path {where!r} from the VI's folder, "
        f"but {why}.",
        node,
    )


def _location(
    ctx: CodeGenContext, node: PrimitiveNode, call: ExtFuncCall
) -> LibraryLocation:
    """The folder a library recorded relative to the VI is in: from the VI's folder,
    one folder up for each empty token after the first."""
    source = (
        ctx.graph.get_vi_source_path(ctx.vi_name)
        if ctx.graph is not None and ctx.vi_name
        else None
    )
    if source is None:
        raise _fail(ctx, node, call, "the VI's file is not known to resolve it from")
    tokens = call.library_path_tokens
    empties = next((i for i, t in enumerate(tokens) if t), len(tokens))
    folder = source.resolve().parent
    for _ in range(empties - 1):
        folder = folder.parent
    rest = tokens[empties:]
    return LibraryLocation(folder.joinpath(*rest[:-1]), call.library or rest[-1])


def _copied_files(candidates: list[Path]) -> list[Path]:
    """``candidates`` (a file, or a folder's files) and, recursively, the libraries
    they need that sit beside them inside the project."""
    ordered: list[Path] = []
    seen: set[Path] = set()
    pending = list(candidates)
    while pending:
        path = pending.pop(0)
        if path in seen:
            continue
        seen.add(path)
        if path.is_dir():
            pending.extend(sorted(p for p in path.rglob("*") if p.is_file()))
            continue
        ordered.append(path)
        data = path.read_bytes()
        pe = library_format(data) is LibraryFormat.PE
        beside = {p.name.lower() if pe else p.name: p for p in path.parent.iterdir()}
        for needed in needed_in(data):
            found = beside.get(needed.lower() if pe else needed)
            if found is not None and found.is_file():
                pending.append(found)
    return ordered


def _project_copy(
    ctx: CodeGenContext, node: PrimitiveNode, call: ExtFuncCall
) -> str | None:
    """Copy the library into the package and return the expression of its path
    from the module, or None when it lies outside the project."""
    location = _location(ctx, node, call)
    resolver = ctx.import_resolver
    folder = (
        resolver.project_file(location.folder, location.folder) if resolver else None
    )
    if resolver is None or folder is None:
        return None
    candidates = [
        location.folder / name
        for name in [
            *expansions(location.name),
            *call.library_path_tokens[-1:],
        ]
        if name and (location.folder / name).exists()
    ]
    if not candidates:
        raise _fail(
            ctx,
            node,
            call,
            f"no file {' or '.join(expansions(location.name))} is in {location.folder}",
        )
    if ctx.data_files is None:
        raise _fail(
            ctx,
            node,
            call,
            "the library is copied into the generated package, and this call "
            "returns only the module's text. Generate with `lvkit generate` to "
            "get the file",
        )
    for path in _copied_files(candidates):
        target = resolver.project_file(location.folder, path)
        if target is not None:
            ctx.data_files[target] = path.read_bytes()
    parts = [p for p in folder.split("/") if p != "."]
    leaf = (
        f"{_LIBRARY_FILE_NAME}({location.name!r})"
        if "*" in location.name
        else repr(location.name)
    )
    joined = ", ".join([*map(repr, parts), leaf])
    return f"os.path.join(os.path.dirname(__file__), {joined})"


def declare_library_file(module: CtypesModule) -> str:
    """Declare ``_library_file`` once in the module; return its name."""
    return module.declare(
        ("helper", _LIBRARY_FILE_NAME),
        _LIBRARY_FILE_NAME,
        lambda n: LIBRARY_FILE_SOURCE,
    )


def loader(call: ExtFuncCall) -> str:
    """The class that loads the library: ``WinDLL`` for a ``stdcall`` node where
    Windows provides it, ``CDLL`` otherwise."""
    if call.calling_convention == "stdcall":
        return 'getattr(ctypes, "WinDLL", ctypes.CDLL)'
    return "ctypes.CDLL"


def static_name(
    ctx: CodeGenContext, node: PrimitiveNode, call: ExtFuncCall, module: CtypesModule
) -> tuple[str, set[str]]:
    """The expression of the library a node's setup names, evaluated when the
    module is imported, and the imports it needs."""
    library = call.library or ""
    imports: set[str] = set()
    expression = None
    if call.library_path_kind is LibraryPathKind.RELATIVE_TO_VI:
        expression = _project_copy(ctx, node, call)
        imports.add("import os")
    if expression is None:
        expression = repr(library)
        imports = set()
        if "*" in library:
            expression = f"{_LIBRARY_FILE_NAME}({library!r})"
    if _LIBRARY_FILE_NAME in expression:
        declare_library_file(module)
        imports |= {"import os", "import sys"}
    return expression, imports


def wired_name(path_var: str, module: CtypesModule) -> tuple[str, set[str]]:
    """The expression of the library a wired ``path in`` names, evaluated when the
    node runs."""
    declare_library_file(module)
    return f"{_LIBRARY_FILE_NAME}({path_var})", {"import os", "import sys"}
