"""Regression test for issue #98.

A ``.lvlib`` member declared ``Type="VI"`` whose ``URL`` is neither a
``.vi`` nor a ``.ctl`` file (a source-metadata anomaly — e.g. a stray
``member.bin``) used to be handed straight to ``load_vi``, which raises
``ValueError`` for any non-``.vi``/``*_BDHb.xml`` path and aborted the
whole ``lvkit index`` run. ``load_lvlib`` (``src/lvkit/graph/loading.py``)
must instead warn and skip that member — no graph node, no stub — while
still loading the rest of the library.
"""

from __future__ import annotations

from pathlib import Path

from lvkit.graph import InMemoryVIGraph, LoadMode

_LVLIB_XML = """<?xml version='1.0' encoding='UTF-8'?>
<Library LVVersion="25008000">
\t<Property Name="NI.Lib.Version" Type="Str">1.0.0.0</Property>
\t<Item Name="member.bin" Type="VI" URL="member.bin"/>
</Library>
"""


def test_nonvi_member_is_skipped_and_reported(tmp_path: Path) -> None:
    lvlib_path = tmp_path / "Example.lvlib"
    lvlib_path.write_text(_LVLIB_XML, encoding="utf-8")
    member_path = tmp_path / "member.bin"
    member_path.write_bytes(b"not a vi file")

    graph = InMemoryVIGraph()
    key = graph.load_lvlib(lvlib_path, LoadMode.NONE)

    # No exception, and the library itself still resolves + loads.
    assert key == str(lvlib_path.resolve())
    assert graph._dep_graph.has_node(key)

    # The non-VI member produced NEITHER a node NOR a stub.
    member_key = str(member_path.resolve())
    assert not graph._dep_graph.has_node(member_key)
    assert member_key not in graph._stubs
    assert list(graph._dep_graph.successors(key)) == []

    # The anomaly was surfaced in the run's report, not swallowed.
    (problem,) = graph.problems()
    assert problem.vi == "Example.lvlib"
    assert "member.bin" in problem.reason
    assert 'Type="VI"' in problem.reason
