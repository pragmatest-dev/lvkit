"""The accessor VIs inside a class's ``Property Definition`` item are members of
the class: they are in its member list, carry its ownership and access facts in
the index, and show in ``structure`` output."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from lvkit.cli import cmd_structure
from lvkit.extract_pool import ExtractionPool
from lvkit.graph import InMemoryVIGraph
from lvkit.index.build import _build_class_fact
from lvkit.load_mode import LoadMode
from lvkit.structure import parse_lvclass
from tests.test_call_identity import _Tree

_CLASS = """<?xml version='1.0' encoding='UTF-8'?>
<LVClass Name="Cls.lvclass" LVVersion="25008000">
\t<Item Name="Run.vi" Type="VI" URL="../Run.vi"/>
\t<Item Name="Folder" Type="Folder">
\t\t<Item Name="Inside.vi" Type="VI" URL="../Inside.vi"/>
\t</Item>
\t<Item Name="Count" Type="Property Definition">
\t\t<Item Name="Read Count.vi" Type="VI" URL="../Read Count.vi">
\t\t\t<Property Name="NI.ClassItem.MethodScope" Type="Int">2</Property>
\t\t</Item>
\t\t<Item Name="Write Count.vi" Type="VI" URL="../Write Count.vi"/>
\t</Item>
</LVClass>
"""


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Tree:
    built = _Tree(tmp_path / "src")
    monkeypatch.setattr("lvkit.graph.loading.extract_vi_xml", built.extract)
    monkeypatch.setattr("lvkit.structure.extract_vi_xml", lambda _p: (None, None, None))
    monkeypatch.setattr(ExtractionPool, "prefetch", lambda self, paths: None)
    (built.root / "Cls").mkdir(parents=True)
    (built.root / "Cls" / "Cls.lvclass").write_text(_CLASS, encoding="utf-8")
    for name in ("Run", "Inside", "Read Count", "Write Count"):
        built.vi(f"Cls/{name}.vi", f"Cls.lvclass:{name}.vi", ["Cls.lvclass"])
    return built


def test_property_definition_vis_are_in_the_class_member_list(tree: _Tree) -> None:
    cls = parse_lvclass(tree.root / "Cls" / "Cls.lvclass")
    assert [(m.name, m.scope, m.is_accessor) for m in cls.methods] == [
        ("Run", "public", False),
        ("Inside", "public", False),
        ("Read Count", "private", True),
        ("Write Count", "public", True),
    ]


def test_property_definition_vis_carry_the_class_facts_the_index_stores(
    tree: _Tree,
) -> None:
    graph = InMemoryVIGraph()
    graph.load_lvclass(tree.root / "Cls" / "Cls.lvclass", LoadMode.NONE)
    facts = {}
    for name in ("Read Count.vi", "Write Count.vi"):
        key = str((tree.root / "Cls" / name).resolve())
        assert graph.get_owning_class(key) == "Cls.lvclass"
        facts[name] = _build_class_fact(graph, key, "Cls.lvclass")
    read, write = facts["Read Count.vi"], facts["Write Count.vi"]
    assert read is not None and write is not None
    assert (read.scope, read.is_accessor, read.accessor_field) == (
        "private",
        True,
        "Count",
    )
    assert (write.scope, write.is_accessor, write.accessor_field) == (
        "public",
        True,
        "Count",
    )


def test_describe_of_the_class_lists_the_property_definition_vis(
    tree: _Tree, capsys: pytest.CaptureFixture[str]
) -> None:
    args = argparse.Namespace(input=str(tree.root / "Cls" / "Cls.lvclass"), json=False)
    assert cmd_structure(args) == 0
    out = capsys.readouterr().out
    assert "    - Read Count (private)" in out
    assert "    - Write Count (public)" in out
