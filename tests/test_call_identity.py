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
from xml.sax.saxutils import escape

import pytest

from lvkit.extract_pool import ExtractionPool
from lvkit.graph import AmbiguousVIReferenceError, InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.models import ConstantNode, VINode
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.load_mode import LoadMode
from lvkit.pipeline import generate_python
from lvkit.render import render_vi_with_subvis

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
    livi_tag: str = "VIPI"
    dyn: tuple[list[str], list[str]] | None = None


def _static(names: list[str], path: list[str], label: str | None = None) -> _Call:
    return _Call(label=label, records=[("IUVI", names, path, 0)])


def _strings(items: list[str]) -> str:
    return "".join(
        f"<String>{escape(i)}</String>" if i else "<String />" for i in items
    )


def _record(tag: str, names: list[str], path: list[str], flag: int, uid: int) -> str:
    return (
        f'<{tag} LinkSaveFlag="{flag}">'
        f"<LinkSaveQualName>{_strings(names)}</LinkSaveQualName>"
        f'<LinkSavePathRef Ident="PTH0" TpVal="1">{_strings(path)}</LinkSavePathRef>'
        f"<LinkOffsetList><Offset>0x{uid:04X}</Offset></LinkOffsetList>"
        f"</{tag}>"
    )


_FIRST_DYN_LINK_UID = 500


def _dyn_record(names: list[str], path: list[str], index: int) -> str:
    """A ``DyOM`` record as pylabview decodes it: the method name carries two
    leading control bytes, and the call is listed by its ``dynLink`` uid."""
    method = f"<![CDATA[&#x01;&#x05;{names[1]}]]>"
    qual = f"<String>{names[0]}</String><String>{method}</String>"
    return (
        '<DyOM LinkSaveFlag="0">'
        f"<LinkSaveQualName>{qual}</LinkSaveQualName>"
        f'<LinkSavePathRef Ident="PTH0" TpVal="1">{_strings(path)}</LinkSavePathRef>'
        f"<APILinkCacheList><Offset>0x{_FIRST_DYN_LINK_UID + index:04X}</Offset>"
        "</APILinkCacheList></DyOM>"
    )


def _node(call: _Call, uid: int) -> str:
    label = (
        f"<label><partID>16</partID><textRec><text>{call.label}</text></textRec>"
        "</label>"
        if call.label
        else ""
    )
    dyn_link = (
        f'<dynLink class="dynLink" uid="{_FIRST_DYN_LINK_UID + uid - 10}" />'
        if call.dyn
        else ""
    )
    return (
        f'<SL__arrayElement class="{call.node_class}" uid="{uid}">{label}'
        f"<termList></termList>{dyn_link}</SL__arrayElement>"
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
    ) + "".join(_dyn_record(*call.dyn, i) for i, call in enumerate(calls) if call.dyn)
    livi_links = "".join(
        _record(call.livi_tag, *call.livi, 0, _FIRST_CALL_UID + i)
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
    # Callers whose recorded path no longer holds the file: found by file name,
    # accepted only when the file's own owning libraries match the record.
    built.vi(
        "caller_moved.vi",
        "caller_moved.vi",
        [],
        [_static(["TestSuite.lvclass", "skip.vi"], ["", "Moved", "skip.vi"])],
    )
    built.vi("Dup1/dup.vi", "dup.vi", [])
    built.vi("Dup2/dup.vi", "dup.vi", [])
    built.vi(
        "caller_dup.vi",
        "caller_dup.vi",
        [],
        [_static(["dup.vi"], ["", "Gone", "dup.vi"])],
    )
    built.vi(
        "twins/caller_pair.vi",
        "caller_pair.vi",
        [],
        [
            _static(["twin.vi"], ["", "One", "twin.vi"]),
            _static(["twin.vi"], ["", "Two", "twin.vi"]),
        ],
    )
    built.vi("Same1/same.vi", "same.vi", [])
    built.vi("Same2/same.vi", "same.vi", [])
    built.vi(
        "caller_same.vi",
        "caller_same.vi",
        [],
        [_static(["same.vi"], ["", "Gone", "same.vi"])],
    )
    # Two files that carry one qualified name (a source VI and a copy of it), each
    # called by its own caller.
    built.vi("twins/One/twin.vi", "twin.vi", [])
    built.vi("twins/Two/twin.vi", "twin.vi", [])
    built.vi(
        "twins/caller_one.vi",
        "caller_one.vi",
        [],
        [_static(["twin.vi"], ["", "One", "twin.vi"])],
    )
    built.vi(
        "twins/caller_two.vi",
        "caller_two.vi",
        [],
        [_static(["twin.vi"], ["", "Two", "twin.vi"])],
    )
    # A static VI reference: labelled with the full qualified name, recorded
    # without the caller's own library.
    built.vi("Lib/target.vi", "Lib.lvlib:target.vi", ["Lib.lvlib"])
    built.vi(
        "Lib/ref_holder.vi",
        "Lib.lvlib:ref_holder.vi",
        ["Lib.lvlib"],
        [
            _Call(
                node_class="statVIRef",
                label="Lib.lvlib:target.vi",
                livi=(["target.vi"], ["", "target.vi"]),
                livi_tag="BSVR",
            )
        ],
    )
    # A static VI reference recorded only by its BDHP link (a bare label), and a
    # Call By Reference whose VI is chosen in the node's setup (an SCVI record).
    built.vi(
        "Lib/bare_ref_holder.vi",
        "Lib.lvlib:bare_ref_holder.vi",
        ["Lib.lvlib"],
        [
            _Call(
                node_class="statVIRef",
                label="target.vi",
                records=[("SVVI", ["target.vi"], ["", "target.vi"], 2)],
            )
        ],
    )
    built.vi(
        "Lib/setup_caller.vi",
        "Lib.lvlib:setup_caller.vi",
        ["Lib.lvlib"],
        [
            _Call(
                node_class="callByRefNode",
                records=[("SCVI", ["target.vi"], ["", "target.vi"], 2)],
            )
        ],
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
    # Dynamic-dispatch calls: two classes' methods named alike, each call listed
    # under its own ``dynLink`` by the record naming its class and method.
    built.vi("Classes/TestCase/id.vi", "TestCase.lvclass:id.vi", ["TestCase.lvclass"])
    built.vi(
        "Classes/TestSuite/id.vi", "TestSuite.lvclass:id.vi", ["TestSuite.lvclass"]
    )
    built.vi(
        "caller_dispatch.vi",
        "caller_dispatch.vi",
        [],
        [
            _Call(
                node_class="dynIUse",
                label="id.vi",
                dyn=(
                    ["TestCase.lvclass", "id.vi"],
                    ["", "Classes", "TestCase", "TestCase.lvclass"],
                ),
            ),
            _Call(
                node_class="dynIUse",
                label="id.vi",
                dyn=(
                    ["TestSuite.lvclass", "id.vi"],
                    ["", "Classes", "TestSuite", "TestSuite.lvclass"],
                ),
            ),
        ],
    )
    # A call-parent node whose record names a class under an unconfigured <vilib>.
    built.vi(
        "caller_parent.vi",
        "caller_parent.vi",
        [],
        [
            _Call(
                node_class="callParentDynIUse",
                label="id.vi",
                dyn=(["Lib.lvclass", "id.vi"], ["<vilib>", "NI", "Lib.lvclass"]),
            )
        ],
    )
    return built


def _load(tree: _Tree, name: str, mode: LoadMode = LoadMode.MINIMAL):
    graph = InMemoryVIGraph()
    key = graph.load_vi(tree.root / name, mode, search_paths=[tree.root])
    assert key is not None
    return graph, key


def _calls(graph: InMemoryVIGraph, key: str) -> list[VINode]:
    return [
        n for n in graph.iter_nodes(key) if isinstance(n, VINode) and n.id != n.vi_path
    ]


def _call(graph: InMemoryVIGraph, key: str) -> VINode:
    (call,) = _calls(graph, key)
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
    one_key = graph.load_vi(tree.root / "twins/caller_one.vi", LoadMode.MINIMAL)
    two_key = graph.load_vi(tree.root / "twins/caller_two.vi", LoadMode.MINIMAL)
    assert one_key is not None and two_key is not None
    one, two = _call(graph, one_key), _call(graph, two_key)
    assert one.qualified_name == two.qualified_name == "twin.vi"
    assert one.callee_key == _key(tree, "twins/One/twin.vi")
    assert two.callee_key == _key(tree, "twins/Two/twin.vi")
    assert graph.loaded_callee_key(one) == _key(tree, "twins/One/twin.vi")
    assert graph.loaded_callee_key(two) == _key(tree, "twins/Two/twin.vi")


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


def test_a_moved_callee_is_the_file_whose_owning_library_matches(tree: _Tree) -> None:
    graph, key = _load(tree, "caller_moved.vi")
    assert _call(graph, key).callee_key == _key(tree, "TestSuite/skip.vi")


def test_a_moved_callee_with_two_matching_files_is_ambiguous(tree: _Tree) -> None:
    with pytest.raises(AmbiguousVIReferenceError, match="dup.vi"):
        _load(tree, "caller_dup.vi")


def test_copies_of_one_file_are_still_two_matching_files(tree: _Tree) -> None:
    with pytest.raises(AmbiguousVIReferenceError, match="same.vi"):
        _load(tree, "caller_same.vi")


def test_a_vi_loaded_without_dependencies_gets_its_callee_on_upgrade(
    tree: _Tree,
) -> None:
    graph, key = _load(tree, "caller_case.vi", LoadMode.NONE)
    assert _call(graph, key).callee_key is None
    graph.load_vi(tree.root / "caller_case.vi", LoadMode.MINIMAL)
    assert _call(graph, key).callee_key == _key(tree, "TestCase/skip.vi")


def test_a_static_vi_reference_names_the_vi_its_caller_records(tree: _Tree) -> None:
    graph, key = _load(tree, "Lib/ref_holder.vi")
    (ref,) = [n for n in graph.iter_nodes(key) if isinstance(n, ConstantNode)]
    assert ref.value == "Lib.lvlib:target.vi"
    assert ref.referenced_vi_key == _key(tree, "Lib/target.vi")


def test_a_static_vi_reference_recorded_by_its_diagram_link_names_its_vi(
    tree: _Tree,
) -> None:
    graph, key = _load(tree, "Lib/bare_ref_holder.vi")
    (ref,) = [n for n in graph.iter_nodes(key) if isinstance(n, ConstantNode)]
    assert ref.referenced_vi_key == _key(tree, "Lib/target.vi")


def test_a_call_by_reference_with_its_vi_chosen_in_the_setup_names_that_vi(
    tree: _Tree,
) -> None:
    graph, key = _load(tree, "Lib/setup_caller.vi")
    call = _call(graph, key)
    assert call.callee_key == _key(tree, "Lib/target.vi")
    assert call.qualified_name == "Lib.lvlib:target.vi"


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


def test_each_dispatch_call_names_the_method_its_own_record_lists(
    tree: _Tree,
) -> None:
    graph, key = _load(tree, "caller_dispatch.vi")
    case, suite = sorted(_calls(graph, key), key=lambda n: n.id)
    assert (case.qualified_name, suite.qualified_name) == (
        "TestCase.lvclass:id.vi",
        "TestSuite.lvclass:id.vi",
    )
    assert case.callee_key == _key(tree, "Classes/TestCase/id.vi")
    assert suite.callee_key == _key(tree, "Classes/TestSuite/id.vi")


def test_a_call_parent_record_under_an_unconfigured_root_names_no_file(
    tree: _Tree,
) -> None:
    graph, key = _load(tree, "caller_parent.vi")
    (call,) = _calls(graph, key)
    assert call.qualified_name == "Lib.lvclass:id.vi"
    assert call.callee_key is None


def test_lvnet_and_describe_list_each_callee_with_its_own_path(tree: _Tree) -> None:
    for caller, owner in (("caller_case", "TestCase"), ("caller_suite", "TestSuite")):
        graph, key = _load(tree, f"{caller}.vi")
        text = render_lvnet(
            build_netlist_from_graph(graph, key), display_name=caller, verbose=True
        )
        assert text.splitlines()[:5] == [
            f"vi {caller} :",
            "  uses :",
            f"    subVI {owner}.lvclass:skip.vi ; ./{owner}/skip.vi",
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


def test_the_layout_does_not_depend_on_the_search_paths(
    tree: _Tree, tmp_path: Path
) -> None:
    """A VI and the project VIs it calls land in the same modules whatever extra
    search paths are given."""
    layouts = []
    for index, search_paths in enumerate(
        ([tree.root], [tree.root, tree.root / "Util", tree.root / "twins"])
    ):
        result = generate_python(
            tree.root / "caller_case.vi",
            tmp_path / f"gen{index}",
            search_paths=search_paths,
        )
        layouts.append(
            {
                Path(key).name: path.relative_to(result["output_dir"])
                for key, path in result["vi_paths"].items()
            }
        )
    assert layouts[0] == layouts[1]
    assert layouts[0]["skip.vi"] == Path("testcase/skip.py")
    assert layouts[0]["caller_case.vi"] == Path("caller_case.py")


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
            f"from .{owner}.skip import skip",
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
    generate_python(tree.root / "twins", out, search_paths=[tree.root])
    imports = {}
    for caller in ("caller_one", "caller_two"):
        source = next(out.rglob(f"{caller}.py")).read_text()
        (line,) = [ln for ln in source.splitlines() if ln.startswith("from .")]
        imports[caller] = line.split(" import ")[0]
    assert imports["caller_one"] != imports["caller_two"]


def test_calls_to_two_files_with_one_qualified_name_are_two_components(
    tree: _Tree,
) -> None:
    graph, key = _load(tree, "twins/caller_pair.vi")
    first, second = sorted(_calls(graph, key), key=lambda n: n.id)
    # One caller cannot link two files of one qualified name, so name the second
    # file on the node directly.
    first.callee_key = _key(tree, "twins/One/twin.vi")
    second.callee_key = _key(tree, "twins/Two/twin.vi")
    names = [c.name for c in build_netlist_from_graph(graph, key).components]
    assert names == ["twin.vi", "twin.vi (2)"]


@pytest.mark.needs_samples
def test_the_docs_diagram_links_each_call_to_its_own_vi_page() -> None:
    root = Path(".lvkit/cache/samples/measurement-plugin-labview")
    vi = root / "Source/Runtime/Clients/Session Management V1/Session Reservation"
    graph = InMemoryVIGraph()
    key = graph.load_vi(
        vi / "Unreserve Sessions.vi", LoadMode.MINIMAL, search_paths=[root]
    )
    assert key is not None
    svg, targets = render_vi_with_subvis(graph, key)
    assert svg is not None and targets
    assert set(targets.values()) <= set(graph.list_vis())
    calls = {graph.loaded_callee_key(n) for n in _calls(graph, key) if n.callee_key} - {
        None
    }
    assert set(targets.values()) == calls
