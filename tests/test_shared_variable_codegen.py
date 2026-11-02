"""A Shared Variable is a module-level name in the package's
``shared_variables`` module; the VIs that name it read and write that name."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.errors import CodeGenError
from lvkit.codegen.package_modules import PackageModules
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import (
    SharedVariableLibrary,
    SharedVariableNode,
    VIContext,
    Wire,
)
from lvkit.models import FPTerminal, LVType, LVTypeKind, SharedVariableKind, Terminal
from lvkit.pipeline import create_import_resolver
from tests.helpers import build_graph

INT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
LIBRARY_KEY = "/lib/Shared.lvlib"
LIBRARY = SharedVariableLibrary(
    qualified_name="Shared.lvlib",
    kind=SharedVariableKind.SINGLE_PROCESS,
    persistent_id="1",
)


def _fp(tid: str, index: int, name: str, direction: str) -> FPTerminal:
    return FPTerminal(
        id=tid,
        index=index,
        direction=direction,
        wiring_rule=1 if direction == "input" else 0,
        name=name,
        is_public=True,
        is_indicator=direction == "output",
        lv_type=INT,
    )


def _node(
    vi: str,
    prefix: str,
    write: bool,
    library: SharedVariableLibrary | None = LIBRARY,
):
    value = Terminal(
        id=f"{prefix}:value",
        index=0,
        direction="input" if write else "output",
        lv_type=INT,
    )
    stamp = Terminal(id=f"{prefix}:stamp", index=3, direction="output")
    return SharedVariableNode(
        id=prefix,
        vi_path=vi,
        name="Shared Variable",
        variable_name="Counter",
        library=library,
        is_write=write,
        terminals=[value] if write else [value, stamp],
    )


def writer(library: SharedVariableLibrary | None = LIBRARY):
    name = "Writer.vi"
    context = VIContext(
        name=name,
        inputs=[_fp("w:in", 0, "value", "input")],
        data_flow=[
            Wire.from_terminals(from_terminal_id="w:in", to_terminal_id="w:value")
        ],
    )
    return name, context, _node(name, "w", write=True, library=library)


def reader():
    name = "Reader.vi"
    context = VIContext(
        name=name,
        outputs=[_fp("r:out", 0, "value", "output"), _fp("r:ts", 1, "stamp", "output")],
        data_flow=[
            Wire.from_terminals(from_terminal_id="r:value", to_terminal_id="r:out"),
            Wire.from_terminals(from_terminal_id="r:stamp", to_terminal_id="r:ts"),
        ],
    )
    return name, context, _node(name, "r", write=False)


def package_graph(vis) -> InMemoryVIGraph:
    """One graph holding all of ``vis``, with the library the nodes name loaded."""
    graph = InMemoryVIGraph()
    graph._dep_graph.add_node(LIBRARY_KEY)
    graph._qname_to_keys[LIBRARY.qualified_name] = [LIBRARY_KEY]
    for name, context, node in vis:
        build_graph(context, name, [node], graph)
    return graph


def generate(vi, resolver=None, graph: InMemoryVIGraph | None = None) -> str:
    name, context, _ = vi
    if graph is None:
        graph = package_graph([vi])
    return build_module(context, name, import_resolver=resolver, graph=graph)


@pytest.fixture
def imported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    yield importlib.import_module
    for name in [m for m in sys.modules if m == "pkg" or m.startswith("pkg.")]:
        del sys.modules[name]


def written_package(tmp_path: Path, vis) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "pkg"
    root.mkdir(parents=True)
    (root / "__init__.py").write_text("")
    graph = package_graph(vis)
    modules = PackageModules.from_graph(graph, [name for name, _, _ in vis])
    paths = {
        "Writer.vi": root / "writer.py",
        "Reader.vi": root / "reader.py",
    }
    sources = {}
    for vi in vis:
        module = vi[0].split(".")[0].lower()
        resolver = create_import_resolver(
            "pkg", root, paths, root / f"{module}.py", package_modules=modules
        )
        sources[module] = generate(vi, resolver, graph)
        (root / f"{module}.py").write_text(sources[module])
    assert [p.name for p in modules.write(root)] == ["shared_variables.py"]
    return root, sources


def test_the_shared_module_does_not_depend_on_the_order_of_the_vis(
    tmp_path: Path,
) -> None:
    forward, _ = written_package(tmp_path / "a", [writer(), reader()])
    backward, _ = written_package(tmp_path / "b", [reader(), writer()])
    assert (forward / "shared_variables.py").read_text() == (
        backward / "shared_variables.py"
    ).read_text()


def test_a_write_in_one_vi_is_read_in_another(tmp_path: Path, imported) -> None:
    root, sources = written_package(tmp_path, [writer(), reader()])
    assert (root / "shared_variables.py").read_text() == (
        "from __future__ import annotations\n"
        "from typing import Any\n"
        "sharedlvlib_counter: Any = 0\n"
        "sharedlvlib_counter_timestamp: float = 0.0"
    )
    assert (
        "from . import shared_variables as shared_variables_module\n"
        in sources["writer"]
    )
    assert "shared_variables_module.sharedlvlib_counter = value\n" in sources["writer"]
    assert (
        "shared_variables_module.sharedlvlib_counter_timestamp = time.time()"
        in sources["writer"]
    )
    assert (
        "counter = shared_variables_module.sharedlvlib_counter\n" in sources["reader"]
    )
    assert "lvkit" not in sources["writer"] + sources["reader"]

    before = imported("pkg.reader").reader()
    assert (before.value, before.stamp) == (0, 0.0)
    imported("pkg.writer").writer(5)
    after = imported("pkg.reader").reader()
    assert after.value == 5
    assert after.stamp > 0.0


def test_a_variable_with_no_library_raises_where_it_would_run() -> None:
    source = generate(writer(library=None))
    assert "raise NotImplementedError(" in source
    assert "records no library" in source


def test_a_variable_needs_a_package() -> None:
    with pytest.raises(CodeGenError, match="no generated package"):
        generate(writer())
