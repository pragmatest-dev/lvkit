"""A call to a class method is declared by the file the class lists as that
method, wherever the class keeps it.

Tiny hand-made classes and callers on disk: a method kept in a subfolder of the
class, a method inherited from a parent class, and a call recorded by the
VI-level link table with its owner chain. Each call is declared once, by the
real file, and the call node's callee is that file.
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit.codegen.errors import MissingVIError
from lvkit.extract_pool import ExtractionPool
from lvkit.graph import AmbiguousVIReferenceError, InMemoryVIGraph
from lvkit.graph.loading import _case_insensitive_match, class_owner_ref
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.load_mode import LoadMode
from lvkit.parser.link_table import _unwrap_name, qualified_link_name
from lvkit.parser.metadata import parse_vipi_from_livi
from lvkit.parser.models import ParsedDependencyRef
from lvkit.parser.vi import _class_owned_name
from lvkit.text_encoding import (
    unwrap_pascal_name,
    xml_text_encoding,
)
from tests.test_call_identity import _Call, _calls, _key, _load, _Tree

_CLASS = """<?xml version='1.0' encoding='UTF-8'?>
<LVClass Name="{name}.lvclass" LVVersion="25008000">
{items}</LVClass>
"""


def _write_class(
    tree: _Tree, folder: str, name: str, members: dict[str, str], parent: str = ""
) -> None:
    """``folder/<name>.lvclass`` listing each member VI at its URL; ``parent`` is
    the relative URL of the parent class file."""
    items = "".join(
        f'\t<Item Name="{vi}" Type="VI" URL="{url}"/>\n' for vi, url in members.items()
    )
    if parent:
        parent_name = parent.rsplit("/", 1)[-1]
        items += f'\t<Item Name="{parent_name}" Type="Parent" URL="{parent}"/>\n'
    path = tree.root / folder / f"{name}.lvclass"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_CLASS.format(name=name, items=items), encoding="utf-8")


def _dispatch(cls: str, method: str, folder: str) -> _Call:
    return _Call(
        node_class="dynIUse",
        label=method,
        dyn=([f"{cls}.lvclass", method], ["", folder, f"{cls}.lvclass"]),
    )


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Tree:
    built = _Tree(tmp_path / "src")
    monkeypatch.setattr("lvkit.graph.loading.extract_vi_xml", built.extract)
    monkeypatch.setattr("lvkit.structure.extract_vi_xml", lambda _p: (None, None, None))
    monkeypatch.setattr(ExtractionPool, "prefetch", lambda self, paths: None)
    # A method the class keeps in a subfolder (not beside the class file).
    _write_class(built, "Sub", "Sub", {"Run.vi": "../impl/Run.vi"})
    built.vi("Sub/impl/Run.vi", "Sub.lvclass:Run.vi", ["Sub.lvclass"])
    built.vi("caller_sub.vi", "caller_sub.vi", [], [_dispatch("Sub", "Run.vi", "Sub")])
    # A method the child inherits from its parent: the child lists nothing.
    _write_class(built, "Base", "Base", {"Stop.vi": "../Stop.vi"})
    built.vi("Base/Stop.vi", "Base.lvclass:Stop.vi", ["Base.lvclass"])
    _write_class(built, "Child", "Child", {}, parent="../../Base/Base.lvclass")
    built.vi(
        "caller_child.vi",
        "caller_child.vi",
        [],
        [_dispatch("Child", "Stop.vi", "Child")],
    )
    # The child overrides a method its parent also lists.
    _write_class(
        built, "Over", "Over", {"Stop.vi": "../Stop.vi"}, "../../Base/Base.lvclass"
    )
    built.vi("Over/Stop.vi", "Over.lvclass:Stop.vi", ["Over.lvclass"])
    built.vi(
        "caller_over.vi", "caller_over.vi", [], [_dispatch("Over", "Stop.vi", "Over")]
    )
    # A file beside a class that is on disk and records the class as its owner but
    # is missing from the class's member list is not the class's method: the class's
    # member list decides, then the parent's.
    _write_class(built, "Sib", "Sib", {}, parent="../../Base/Base.lvclass")
    built.vi("Sib/Stop.vi", "Sib.lvclass:Stop.vi", ["Sib.lvclass"])
    built.vi("caller_sib.vi", "caller_sib.vi", [], [_dispatch("Sib", "Stop.vi", "Sib")])
    # The same in a subfolder of the class.
    _write_class(built, "Deep", "Deep", {}, parent="../../Base/Base.lvclass")
    built.vi("Deep/sub/Stop.vi", "Deep.lvclass:Stop.vi", ["Deep.lvclass"])
    built.vi(
        "caller_deep.vi", "caller_deep.vi", [], [_dispatch("Deep", "Stop.vi", "Deep")]
    )
    # A file beside the class that another class owns is not the class's method.
    _write_class(built, "Wrong", "Wrong", {}, parent="../../Base/Base.lvclass")
    built.vi("Wrong/Stop.vi", "Other.lvclass:Stop.vi", ["Other.lvclass"])
    built.vi(
        "caller_wrong.vi",
        "caller_wrong.vi",
        [],
        [_dispatch("Wrong", "Stop.vi", "Wrong")],
    )
    # A class that is not on disk, and a file elsewhere that records it as its
    # owner: nothing is looked up by name, so the method is missing.
    built.vi("Abs/sub/Stop.vi", "Abs.lvclass:Stop.vi", ["Abs.lvclass"])
    built.vi("caller_abs.vi", "caller_abs.vi", [], [_dispatch("Abs", "Stop.vi", "Abs")])
    # A class whose file name exists twice, differing only in case.
    _write_class(built, "Amb", "amb", {"Run.vi": "../Run.vi"})
    _write_class(built, "Amb", "AMB", {"Run.vi": "../Run.vi"})
    built.vi("caller_amb.vi", "caller_amb.vi", [], [_dispatch("Amb", "Run.vi", "Amb")])
    # A method no class of the chain lists and no file records.
    built.vi(
        "caller_gone.vi", "caller_gone.vi", [], [_dispatch("Sub", "Gone.vi", "Sub")]
    )
    return built


def _uses(tree: _Tree, name: str) -> list[str]:
    graph, key = _load(tree, name)
    text = render_lvnet(build_netlist_from_graph(graph, key), display_name="X")
    return [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip().startswith("subVI") and " ; " in ln
    ]


def test_method_in_a_class_subfolder_is_declared_once_by_its_file(tree: _Tree) -> None:
    graph, key = _load(tree, "caller_sub.vi")
    declared = [
        (d.qualified, d.path)
        for d in build_netlist_from_graph(graph, key).dependencies
        if d.kind.value == "subVI"
    ]
    assert declared == [("Sub.lvclass:Run.vi", "./Sub/impl/Run.vi")]
    (call,) = _calls(graph, key)
    assert call.callee_key == _key(tree, "Sub/impl/Run.vi")
    assert not any(k.endswith("/Sub/Run.vi") for k in graph.get_vi_dependencies(key))


def test_inherited_method_is_declared_by_the_parents_file(tree: _Tree) -> None:
    graph, key = _load(tree, "caller_child.vi")
    declared = [
        (d.qualified, d.path)
        for d in build_netlist_from_graph(graph, key).dependencies
        if d.kind.value == "subVI"
    ]
    assert declared == [("Base.lvclass:Stop.vi", "./Base/Stop.vi")]
    (call,) = _calls(graph, key)
    assert call.callee_key == _key(tree, "Base/Stop.vi")


def test_method_the_class_declares_itself_is_not_the_parents(tree: _Tree) -> None:
    graph, key = _load(tree, "caller_over.vi")
    (call,) = _calls(graph, key)
    assert call.callee_key == _key(tree, "Over/Stop.vi")
    assert _uses(tree, "caller_over.vi") == [
        "subVI Over.lvclass:Stop.vi ; ./Over/Stop.vi"
    ]


def _plain(name: str) -> bytes:
    raw = name.encode("ascii")
    return bytes([len(raw)]) + raw


def _wrapped(name: str) -> bytes:
    raw = name.encode("ascii")
    return bytes([2 + len(raw), 0x01, len(raw)]) + raw


def _vipi(chain: list[str], path: list[str]) -> bytes:
    names = b"".join(_plain(n) for n in chain[:-1]) + _wrapped(chain[-1])
    comp = b"".join(_plain(t) for t in path)
    pth0 = (
        b"PTH0"
        + struct.pack(">I", len(comp) + 4)
        + struct.pack(">H", 1)
        + struct.pack(">H", len(path))
        + comp
    )
    return b"VIPI" + struct.pack(">I", len(chain)) + names + pth0


def _livi(tree: _Tree, vi: str, records: list[bytes]) -> None:
    slot = tree.extract(tree.root / vi)[2]
    slot.with_name(slot.stem + "_LIvi.bin").write_bytes(b"".join(records))


def test_vipi_keeps_its_owner_chain_and_path(tmp_path: Path) -> None:
    path = tmp_path / "vi_LIvi.bin"
    path.write_bytes(
        _vipi(["Lib.lvlib", "Cls.lvclass", "Run.vi"], ["", "Cls", "Cls.lvclass"])
        + _vipi(["Go.vi"], ["", "Other", "Other.lvclass"])
    )
    refs = parse_vipi_from_livi(path)
    assert [(r.qualified_name, r.path_tokens, r.path_type) for r in refs] == [
        ("Lib.lvlib:Cls.lvclass:Run.vi", ["", "Cls", "Cls.lvclass"], 1),
        ("Other.lvclass:Go.vi", ["", "Other", "Other.lvclass"], 1),
    ]


def test_vipi_with_an_owner_chain_declares_the_real_file(tree: _Tree) -> None:
    _livi(
        tree,
        "caller_sub.vi",
        [_vipi(["Lib.lvlib", "Sub.lvclass", "Run.vi"], ["", "Sub", "Sub.lvclass"])],
    )
    graph, key = _load(tree, "caller_sub.vi")
    declared = [
        (d.qualified, d.path)
        for d in build_netlist_from_graph(graph, key).dependencies
        if d.kind.value == "subVI"
    ]
    # The block-diagram record and the VI-level record are one call: one file.
    assert declared == [("Sub.lvclass:Run.vi", "./Sub/impl/Run.vi")]
    (call,) = _calls(graph, key)
    assert call.callee_key == _key(tree, "Sub/impl/Run.vi")


def test_vipi_alone_resolves_through_the_class_it_names(tree: _Tree) -> None:
    tree.vi("vipi_only.vi", "vipi_only.vi", [])
    _livi(
        tree,
        "vipi_only.vi",
        [_vipi(["Child.lvclass", "Stop.vi"], ["", "Child", "Child.lvclass"])],
    )
    graph, key = _load(tree, "vipi_only.vi")
    assert sorted(graph.get_vi_dependencies(key)) == sorted(
        [_key(tree, "Base/Stop.vi"), _key(tree, "Child/Child.lvclass")]
    )


def test_vipi_naming_only_the_method_never_finds_the_callers_own_file(
    tree: _Tree,
) -> None:
    """A method of another class that shares the caller's own class method name
    resolves through the class its record's path names, not the caller's."""
    tree.vi("Sub/impl/Stop.vi", "Sub.lvclass:Stop.vi", ["Sub.lvclass"])
    _write_class(
        tree, "Sub", "Sub", {"Run.vi": "../impl/Run.vi", "Stop.vi": "../impl/Stop.vi"}
    )
    _livi(
        tree,
        "Sub/impl/Stop.vi",
        [_vipi(["Stop.vi"], ["", "", "", "Base", "Base.lvclass"])],
    )
    graph = InMemoryVIGraph()
    key = graph.load_vi(tree.root / "Sub/impl/Stop.vi", LoadMode.MINIMAL)
    assert key is not None
    assert _key(tree, "Sub/impl/Stop.vi") not in graph.get_vi_dependencies(key)
    assert _key(tree, "Base/Stop.vi") in graph.get_vi_dependencies(key)


def test_record_naming_only_a_method_under_a_class_path_is_owned_by_the_class() -> None:
    assert _class_owned_name("Go.vi", ["", "Cls", "Cls.lvclass"]) == "Cls.lvclass:Go.vi"
    assert _class_owned_name("Lib.lvlib:Go.vi", ["", "Cls.lvclass"]) == (
        "Lib.lvlib:Go.vi"
    )
    assert _class_owned_name("Go.vi", ["", "Cls", "Go.vi"]) == "Go.vi"


def test_file_beside_a_class_on_disk_that_it_does_not_list_is_not_the_method(
    tree: _Tree,
) -> None:
    graph, key = _load(tree, "caller_sib.vi")
    (call,) = _calls(graph, key)
    assert call.callee_key == _key(tree, "Base/Stop.vi")


def test_method_no_class_of_the_chain_lists_has_no_invented_path(tree: _Tree) -> None:
    graph, key = _load(tree, "caller_gone.vi")
    declared = [
        (d.qualified, d.path)
        for d in build_netlist_from_graph(graph, key).dependencies
        if d.kind.value == "subVI"
    ]
    assert declared == [("Sub.lvclass:Gone.vi", None)]
    (call,) = _calls(graph, key)
    assert call.callee_key is None


def test_long_method_name_drops_its_printable_length_byte() -> None:
    name = "Initialize MeasurementLink Session.vi"
    assert len(name) == 37
    for wrapper in ("&#x01;%", "\x01%"):
        assert (
            qualified_link_name(["Cls.lvclass", wrapper + name], 0, None, "cp1252")
            == f"Cls.lvclass:{name}"
        )
    assert qualified_link_name(
        ["Cls.lvclass", "&#x01;&#x05;Run.vi"], 0, None, "cp1252"
    ) == ("Cls.lvclass:Run.vi")


def test_member_in_a_class_subfolder_the_class_does_not_list_is_the_ancestors(
    tree: _Tree,
) -> None:
    graph, key = _load(tree, "caller_deep.vi")
    (call,) = _calls(graph, key)
    assert call.callee_key == _key(tree, "Base/Stop.vi")


def test_method_of_a_class_not_at_its_recorded_path_is_missing(
    tree: _Tree,
) -> None:
    """The class is not at the path the call records, so its method is missing
    — the file elsewhere that records the class as its owner is never searched
    for by name. The missing class keeps its recorded path."""
    graph, key = _load(tree, "caller_abs.vi")
    (call,) = _calls(graph, key)
    assert call.callee_key is None
    absent_class = _key(tree, "Abs/Abs.lvclass")
    assert absent_class in graph.get_vi_dependencies(key)
    assert graph.is_stub(absent_class)
    assert _key(tree, "Abs/sub/Stop.vi") not in graph.list_vis()


def test_files_of_a_class_name_differing_only_in_case_make_the_call_missing(
    tree: _Tree,
) -> None:
    if len([p for p in (tree.root / "Amb").iterdir() if p.suffix == ".lvclass"]) < 2:
        pytest.skip("case-insensitive filesystem")
    graph, key = _load(tree, "caller_amb.vi")
    (call,) = _calls(graph, key)
    assert call.callee_key is None
    (reason,) = [
        r for d in graph.get_vi_dependencies(key) if (r := graph.stub_reason(d))
    ]
    assert "differ only in case" in reason
    assert "./Amb/AMB.lvclass" in reason and "./Amb/amb.lvclass" in reason
    text = render_lvnet(build_netlist_from_graph(graph, key), display_name="X")
    assert "subVI Amb.lvclass:Run.vi" in text
    assert str(tree.root) not in text and str(tree.root) not in reason


def test_file_beside_the_class_owned_by_another_class_is_not_the_method(
    tree: _Tree,
) -> None:
    graph, key = _load(tree, "caller_wrong.vi")
    (call,) = _calls(graph, key)
    assert call.callee_key == _key(tree, "Base/Stop.vi")


def test_unlisted_method_of_two_classes_with_one_name_is_two_dependencies(
    tree: _Tree,
) -> None:
    for folder in ("A", "B"):
        _write_class(tree, f"{folder}/Sub", "Sub", {})
        tree.vi(
            f"{folder}/caller.vi",
            "caller.vi",
            [],
            [_dispatch("Sub", "Gone.vi", "Sub")],
        )
    graph = InMemoryVIGraph()
    key_a = graph.load_vi(tree.root / "A/caller.vi", LoadMode.MINIMAL)
    key_b = graph.load_vi(tree.root / "B/caller.vi", LoadMode.MINIMAL)
    assert key_a is not None and key_b is not None
    deps_a = set(graph.get_vi_dependencies(key_a))
    deps_b = set(graph.get_vi_dependencies(key_b))
    stub_a = "Sub.lvclass@" + _key(tree, "A/Sub/Sub.lvclass") + ":Gone.vi"
    stub_b = "Sub.lvclass@" + _key(tree, "B/Sub/Sub.lvclass") + ":Gone.vi"
    assert stub_a in deps_a and stub_b in deps_b
    assert not deps_a & deps_b


def _ref(qname: str, tokens: list[str]) -> ParsedDependencyRef:
    return ParsedDependencyRef(
        name=tokens[-1], path_tokens=tokens, qualified_name=qname
    )


def test_class_owner_chain_must_end_in_the_class_the_path_names() -> None:
    tokens = ["", "Cls", "Cls.lvclass"]
    owner = class_owner_ref("Cls.lvclass:Go.vi", _ref("Cls.lvclass:Go.vi", tokens))
    assert owner is not None and owner.qualified_name == "Cls.lvclass"
    owner = class_owner_ref("Lib.lvlib:CLS.LVCLASS:Go.vi", _ref("x", tokens))
    assert owner is not None and owner.qualified_name == "Lib.lvlib:CLS.LVCLASS"
    owner = class_owner_ref("Go.vi", _ref("Go.vi", tokens))
    assert owner is not None and owner.qualified_name == "Cls.lvclass"
    assert class_owner_ref("Lib.lvlib:Go.vi", _ref("Lib.lvlib:Go.vi", tokens)) is None
    assert class_owner_ref("Other.lvclass:Go.vi", _ref("x", tokens)) is None
    upper = ["", "Cls", "Cls.LVCLASS"]
    assert class_owner_ref("Cls.lvclass:Go.vi", _ref("x", upper)) is not None
    assert _class_owned_name("Go.vi", upper) == "Cls.LVCLASS:Go.vi"


def test_files_differing_only_in_case_are_ambiguous(tmp_path: Path) -> None:
    (tmp_path / "Foo.lvclass").write_text("a")
    (tmp_path / "FOO.lvclass").write_text("b")
    if len(list(tmp_path.iterdir())) < 2:
        pytest.skip("case-insensitive filesystem")
    with pytest.raises(AmbiguousVIReferenceError, match="differ only in case"):
        _case_insensitive_match(tmp_path, "foo.LVCLASS")


def test_the_one_file_differing_in_case_is_the_match(tmp_path: Path) -> None:
    (tmp_path / "Daqmx Module runtime.lvclass").write_text("a")
    (tmp_path / "other.lvclass").write_text("b")
    match = _case_insensitive_match(tmp_path, "DAQmx Module Runtime.lvclass")
    assert match == tmp_path / "Daqmx Module runtime.lvclass"
    assert _case_insensitive_match(tmp_path, "missing.lvclass") is None


def _encoded_wrapped(name: str, encoding: str) -> bytes:
    raw = name.encode(encoding)
    return bytes([2 + len(raw), 0x01, len(raw)]) + raw


def test_unwrap_pascal_name_keeps_only_the_name_bytes() -> None:
    name = b"Initialize MeasurementLink Session.vi"
    outer = bytes([0x01, 0x25]) + name
    assert outer[:2] == b"\x01%" and len(name) == 0x25
    assert unwrap_pascal_name(outer) == name
    assert unwrap_pascal_name(b"Cls.lvclass") == b"Cls.lvclass"
    assert unwrap_pascal_name(b"\x01\x09Run.vi") == b"\x01\x09Run.vi"


@pytest.mark.parametrize("name", ["Größe über.vi", "Ä" * 130 + ".vi"])
def test_vipi_unwraps_a_non_ascii_or_long_method_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv("LVKIT_TEXT_ENCODING", "cp1252")
    path = tmp_path / "vi_LIvi.bin"
    names = _plain("Cls.lvclass") + _encoded_wrapped(name, "cp1252")
    comp = b"".join(_plain(t) for t in ["", "Cls", "Cls.lvclass"])
    pth0 = b"PTH0" + struct.pack(">I", len(comp) + 4) + struct.pack(">HH", 1, 3) + comp
    path.write_bytes(b"VIPI" + struct.pack(">I", 2) + names + pth0)
    (ref,) = parse_vipi_from_livi(path)
    assert ref.qualified_name == f"Cls.lvclass:{name}"


@pytest.mark.parametrize(
    ("encoding", "name"),
    [
        ("cp1252", "Größe über.vi"),
        ("cp1252", "Ä" * 130 + ".vi"),
        ("cp932", "テスト.vi"),
        ("cp932", "日本語のメソッド.vi"),
    ],
)
def test_xml_text_name_unwraps_by_byte_length(encoding: str, name: str) -> None:
    length = len(name.encode(encoding))
    for prefix in ("\x01", "&#x01;"):
        text = prefix + bytes([length]).decode(encoding) + name
        assert qualified_link_name(["Cls.lvclass", text], 0, None, encoding) == (
            f"Cls.lvclass:{name}"
        )
        entity = f"{prefix}&#x{length:02X};{name}"
        assert qualified_link_name(["Cls.lvclass", entity], 0, None, encoding) == (
            f"Cls.lvclass:{name}"
        )


def test_xml_text_name_that_is_not_in_the_encoding_is_left_as_it_is() -> None:
    text = "&#x01;&#x03;テスト"
    assert _unwrap_name(text, "cp1252") == text
    assert _unwrap_name(text, "cp932") == text
    assert _unwrap_name("&#x01;&#x06;テスト", "cp932") == "テスト"


def test_xml_text_encoding_is_the_one_the_extractor_normalizes_with() -> None:
    root = ET.fromstring(
        '<RSRC><vers><Section><Version Language="14"/></Section></vers></RSRC>'
    )
    assert xml_text_encoding(root) == "cp932"


def test_missing_vi_gap_names_the_reason_the_file_is_not_known() -> None:
    plain = MissingVIError("Gone.vi", "./Gone.vi")
    assert "not found at the path its caller records" in plain.summary()
    reason = "Several files. Files: ./A/Dup.vi, ./B/Dup.vi."
    why = MissingVIError("Dup.vi", None, reason)
    assert why.summary() == f"The VI 'Dup.vi' was not found: {reason}"
