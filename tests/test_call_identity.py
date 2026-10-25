"""A called VI is identified by its path, never by its bare file name.

Tiny hand-made VIs on disk: two classes each declare a method ``skip.vi``, and
one caller of each records the file it calls in its block-diagram link table.
Every view must resolve each call to its own file.
"""

from __future__ import annotations

import importlib
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from lvkit.extract_pool import ExtractionPool
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.models import VINode
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.load_mode import LoadMode
from lvkit.pipeline import generate_python

_FIRST_CALL_UID = 10


@dataclass
class _Call:
    """One call node on a caller's diagram and the link-table records naming it.

    ``records`` are ``(tag, qualified name parts, path tokens, flag)`` of the
    block-diagram link table; ``livi`` is the record the VI-level table holds."""

    node_class: str = "iUse"
    label: str | None = None
    records: list[tuple[str, list[str], list[str], int]] = field(default_factory=list)
    livi: tuple[list[str], list[str]] | None = None


def _static(names: list[str], path: list[str], label: str | None = None) -> _Call:
    return _Call(label=label, records=[("IUVI", names, path, 0)])


def _strings(items: list[str]) -> str:
    return "".join(f"<String>{i}</String>" if i else "<String />" for i in items)


def _record(tag: str, names: list[str], path: list[str], flag: int, uid: int) -> str:
    return (
        f'<{tag} LinkSaveFlag="{flag}">'
        f"<LinkSaveQualName>{_strings(names)}</LinkSaveQualName>"
        f'<LinkSavePathRef Ident="PTH0" TpVal="1">{_strings(path)}</LinkSavePathRef>'
        f"<LinkOffsetList><Offset>0x{uid:04X}</Offset></LinkOffsetList>"
        f"</{tag}>"
    )


def _node(call: _Call, uid: int) -> str:
    label = (
        f"<label><partID>16</partID><textRec><text>{call.label}</text></textRec>"
        "</label>"
        if call.label
        else ""
    )
    return (
        f'<SL__arrayElement class="{call.node_class}" uid="{uid}">{label}'
        "<termList></termList></SL__arrayElement>"
    )


def _bd(calls: list[_Call]) -> str:
    nodes = "".join(_node(c, _FIRST_CALL_UID + i) for i, c in enumerate(calls))
    return (
        "<?xml version='1.0' encoding='utf-8'?>"
        '<SL__rootObject class="oHExt" uid="1"><root class="diag" uid="2">'
        f"<zPlaneList>{nodes}</zPlaneList></root></SL__rootObject>"
    )


def _main(qualified: str, owners: list[str], calls: list[_Call]) -> str:
    libs = "".join(f"<Library>{o}</Library>" for o in owners)
    bd_links = "".join(
        _record(tag, names, path, flag, _FIRST_CALL_UID + i)
        for i, call in enumerate(calls)
        for tag, names, path, flag in call.records
    )
    livi_links = "".join(
        _record("VIPI", *call.livi, 0, _FIRST_CALL_UID + i)
        for i, call in enumerate(calls)
        if call.livi
    )
    return (
        "<?xml version='1.0' encoding='utf-8'?><VI>"
        f'<LIvi><Section><LVIN Unk1="{qualified}" />{livi_links}</Section></LIvi>'
        f"<LIBN><Section>{libs}</Section></LIBN>"
        f'<LIbd><Section Index="0"><BDHP Unk1="{qualified}">{bd_links}</BDHP>'
        "</Section></LIbd></VI>"
    )


class _Tree:
    """On-disk placeholder ``.vi`` files plus the XML a fake extractor returns."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._xml: dict[Path, tuple[Path, Path | None, Path]] = {}

    def vi(
        self,
        rel: str,
        qualified: str,
        owners: list[str],
        calls: list[_Call] | None = None,
    ) -> Path:
        """A VI at ``root/rel`` with the given call nodes."""
        calls = calls or []
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        slot = self.root / "_xml" / rel.replace("/", "__").replace(".", "_")
        slot.mkdir(parents=True)
        (slot / "vi_BDHb.xml").write_text(_bd(calls))
        (slot / "vi.xml").write_text(_main(qualified, owners, calls))
        self._xml[path.resolve()] = (slot / "vi_BDHb.xml", None, slot / "vi.xml")
        return path

    def extract(self, path: Path) -> tuple[Path, Path | None, Path]:
        return self._xml[Path(path).resolve()]


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Tree:
    built = _Tree(tmp_path / "src")
    monkeypatch.setattr("lvkit.graph.loading.extract_vi_xml", built.extract)
    monkeypatch.setattr(ExtractionPool, "prefetch", lambda self, paths: None)
    built.vi("TestCase/skip.vi", "TestCase.lvclass:skip.vi", ["TestCase.lvclass"])
    built.vi("TestSuite/skip.vi", "TestSuite.lvclass:skip.vi", ["TestSuite.lvclass"])
    built.vi("Util/unique.vi", "unique.vi", [])
    built.vi(
        "caller_case.vi",
        "caller_case.vi",
        [],
        [_static(["TestCase.lvclass", "skip.vi"], ["", "TestCase", "skip.vi"])],
    )
    built.vi(
        "caller_suite.vi",
        "caller_suite.vi",
        [],
        [_static(["TestSuite.lvclass", "skip.vi"], ["", "TestSuite", "skip.vi"])],
    )
    built.vi(
        "caller_unique.vi",
        "caller_unique.vi",
        [],
        [_static(["unique.vi"], ["", "Util", "unique.vi"])],
    )
    built.vi(
        "caller_absent.vi",
        "caller_absent.vi",
        [],
        [_static(["Gone.lvclass", "gone.vi"], ["", "Gone", "gone.vi"])],
    )
    # Two files that carry one qualified name (a source VI and a copy of it), each
    # called by its own caller.
    built.vi("One/twin.vi", "twin.vi", [])
    built.vi("Two/twin.vi", "twin.vi", [])
    built.vi(
        "caller_one.vi",
        "caller_one.vi",
        [],
        [_static(["twin.vi"], ["", "One", "twin.vi"])],
    )
    built.vi(
        "caller_two.vi",
        "caller_two.vi",
        [],
        [_static(["twin.vi"], ["", "Two", "twin.vi"])],
    )
    # A recursive call: a record that names no VI and has no path.
    built.vi("recur.vi", "recur.vi", [], [_Call(records=[("IUVI", [], [], 1)])])
    # A polymorphic call: the polymorphic VI and the variant it resolves to.
    built.vi("Poly/poly.vi", "poly.vi", [])
    built.vi("Poly/poly_int.vi", "poly_int.vi", [])
    built.vi(
        "caller_poly.vi",
        "caller_poly.vi",
        [],
        [
            _Call(
                node_class="polyIUse",
                label="poly.vi",
                records=[
                    ("PUPV", ["poly.vi"], ["", "Poly", "poly.vi"], 0),
                    ("IUVI", ["poly_int.vi"], ["", "Poly", "poly_int.vi"], 0),
                ],
            )
        ],
    )
    # A dynamic-dispatch call the block-diagram link table does not record: its
    # file is the dependency of that name the VI-level table lists.
    built.vi("Cls/method.vi", "Cls.lvclass:method.vi", ["Cls.lvclass"])
    built.vi(
        "caller_dispatch.vi",
        "caller_dispatch.vi",
        [],
        [
            _Call(
                node_class="dynIUse",
                label="method.vi",
                livi=(["Cls.lvclass", "method.vi"], ["", "Cls", "method.vi"]),
            )
        ],
    )
    return built


def _load(tree: _Tree, name: str, mode: LoadMode = LoadMode.MINIMAL):
    graph = InMemoryVIGraph()
    key = graph.load_vi(tree.root / name, mode, search_paths=[tree.root])
    assert key is not None
    return graph, key


def _call(graph: InMemoryVIGraph, key: str) -> VINode:
    (call,) = [
        n for n in graph.iter_nodes(key) if isinstance(n, VINode) and n.id != n.vi_path
    ]
    return call


def _key(tree: _Tree, rel: str) -> str:
    return str((tree.root / rel).resolve())


def test_each_same_named_method_call_names_its_own_file(tree: _Tree) -> None:
    for caller, callee in (
        ("caller_case.vi", "TestCase/skip.vi"),
        ("caller_suite.vi", "TestSuite/skip.vi"),
    ):
        graph, key = _load(tree, caller)
        call = _call(graph, key)
        assert call.callee_key == _key(tree, callee)
        assert call.variant_key is None
        assert graph.loaded_callee_key(call) == _key(tree, callee)


def test_both_callers_in_one_graph_keep_their_own_callee(tree: _Tree) -> None:
    graph = InMemoryVIGraph()
    case_key = graph.load_vi(tree.root / "caller_case.vi", LoadMode.MINIMAL)
    suite_key = graph.load_vi(tree.root / "caller_suite.vi", LoadMode.MINIMAL)
    assert case_key is not None and suite_key is not None
    assert _call(graph, case_key).callee_key == _key(tree, "TestCase/skip.vi")
    assert _call(graph, suite_key).callee_key == _key(tree, "TestSuite/skip.vi")


def test_two_files_with_one_qualified_name_are_told_apart_by_path(
    tree: _Tree,
) -> None:
    graph = InMemoryVIGraph()
    one_key = graph.load_vi(tree.root / "caller_one.vi", LoadMode.MINIMAL)
    two_key = graph.load_vi(tree.root / "caller_two.vi", LoadMode.MINIMAL)
    assert one_key is not None and two_key is not None
    one, two = _call(graph, one_key), _call(graph, two_key)
    assert one.qualified_name == two.qualified_name == "twin.vi"
    assert one.callee_key == _key(tree, "One/twin.vi")
    assert two.callee_key == _key(tree, "Two/twin.vi")
    assert graph.loaded_callee_key(one) == _key(tree, "One/twin.vi")
    assert graph.loaded_callee_key(two) == _key(tree, "Two/twin.vi")


def test_a_unique_name_call_names_its_file(tree: _Tree) -> None:
    graph, key = _load(tree, "caller_unique.vi")
    call = _call(graph, key)
    assert call.callee_key == _key(tree, "Util/unique.vi")
    assert call.qualified_name == "unique.vi"


def test_an_absent_callee_gets_the_path_its_caller_recorded(tree: _Tree) -> None:
    graph, key = _load(tree, "caller_absent.vi")
    call = _call(graph, key)
    assert call.callee_key == _key(tree, "Gone/gone.vi")
    assert graph.loaded_callee_key(call) is None


def test_a_vi_loaded_without_dependencies_gets_its_callee_on_upgrade(
    tree: _Tree,
) -> None:
    graph, key = _load(tree, "caller_case.vi", LoadMode.NONE)
    assert _call(graph, key).callee_key is None
    graph.load_vi(tree.root / "caller_case.vi", LoadMode.MINIMAL)
    assert _call(graph, key).callee_key == _key(tree, "TestCase/skip.vi")


def test_a_recursive_call_names_its_own_vi(tree: _Tree) -> None:
    graph, key = _load(tree, "recur.vi")
    call = _call(graph, key)
    assert call.callee_key == key
    assert graph.loaded_callee_key(call) == key


def test_a_polymorphic_call_names_the_polymorphic_vi_and_its_variant(
    tree: _Tree,
) -> None:
    graph, key = _load(tree, "caller_poly.vi")
    call = _call(graph, key)
    assert call.callee_key == _key(tree, "Poly/poly.vi")
    assert call.variant_key == _key(tree, "Poly/poly_int.vi")
    assert graph.loaded_callee_key(call) == _key(tree, "Poly/poly_int.vi")


def test_a_dispatch_call_names_the_dependency_of_its_method_name(
    tree: _Tree,
) -> None:
    graph, key = _load(tree, "caller_dispatch.vi")
    call = _call(graph, key)
    assert call.callee_key == _key(tree, "Cls/method.vi")


def test_lvnet_and_describe_list_each_callee_with_its_own_path(tree: _Tree) -> None:
    for caller, owner in (("caller_case", "TestCase"), ("caller_suite", "TestSuite")):
        graph, key = _load(tree, f"{caller}.vi")
        text = render_lvnet(
            build_netlist_from_graph(graph, key), display_name=caller, verbose=True
        )
        assert text.splitlines()[:5] == [
            f"vi {caller} :",
            "  uses :",
            f"    subVI skip.vi ; ./{owner}/skip.vi",
            "  block-diagram :",
            f"    subVI {owner}.lvclass:skip_10 : {owner}.lvclass:skip.vi",
        ]
        described = describe_vi(graph, key)
        assert f"## Dependencies\n  {owner}.lvclass:skip.vi: () -> ()" in described


def test_the_unique_name_call_reads_the_same_in_every_view(tree: _Tree) -> None:
    graph, key = _load(tree, "caller_unique.vi")
    text = render_lvnet(
        build_netlist_from_graph(graph, key), display_name="caller_unique", verbose=True
    )
    assert text.splitlines()[:5] == [
        "vi caller_unique :",
        "  uses :",
        "    subVI unique.vi ; ./Util/unique.vi",
        "  block-diagram :",
        "    subVI unique_10 : unique.vi",
    ]
    assert "## Dependencies\n  unique.vi: () -> ()" in describe_vi(graph, key)


def _generate(tree: _Tree, tmp_path: Path, caller: str) -> Path:
    out = tmp_path / "gen" / caller
    generate_python(tree.root / f"{caller}.vi", out, search_paths=[tree.root])
    return out / caller


def _run(package_dir: Path, module: str) -> types.ModuleType:
    """Import a generated module as part of its package, with its siblings."""
    sys.path.insert(0, str(package_dir.parent))
    try:
        return importlib.import_module(f"{package_dir.name}.{module}")
    finally:
        sys.path.remove(str(package_dir.parent))
        for name in [n for n in sys.modules if n.startswith(f"{package_dir.name}.")]:
            del sys.modules[name]
        sys.modules.pop(package_dir.name, None)


def test_each_generated_caller_imports_its_own_callee_and_runs(
    tree: _Tree, tmp_path: Path
) -> None:
    ran: list[str] = []
    for caller, owner in (("caller_case", "testcase"), ("caller_suite", "testsuite")):
        package = _generate(tree, tmp_path, caller)
        source = (package / f"{caller}.py").read_text()
        assert source.splitlines()[2:] == [
            f"from .{owner}lvclass.{owner}lvclass_skip import skip",
            "",
            f"def {caller}():",
            "    skip()",
        ]
        getattr(_run(package, caller), caller)()
        ran.append(caller)
    assert ran == ["caller_case", "caller_suite"]


def test_callers_of_two_files_with_one_name_import_two_modules(
    tree: _Tree, tmp_path: Path
) -> None:
    out = tmp_path / "gen" / "twins"
    generate_python(tree.root, out, search_paths=[tree.root])
    imports = {}
    for caller in ("caller_one", "caller_two"):
        source = next(out.rglob(f"{caller}.py")).read_text()
        (line,) = [ln for ln in source.splitlines() if ln.startswith("from .")]
        imports[caller] = line.split(" import ")[0]
    assert imports["caller_one"] != imports["caller_two"]
