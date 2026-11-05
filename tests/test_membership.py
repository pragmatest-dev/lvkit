"""Class membership: the class file's list decides, in every load mode, and a
class's method list marks overrides and inherited methods
(``graph/membership.py``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.loading import LoadMode
from lvkit.graph.membership import MethodOrigin
from lvkit.graph.node_kinds import EdgeRel
from tests.corpus_graphs import load_corpus_vi

pytestmark = pytest.mark.needs_samples

_SAMPLES = Path(".lvkit/cache/samples")
_ANTS = _SAMPLES / "ni-labview-icon-editor/resource/plugins/NIIconEditor/Class/Ants"
_AF = _SAMPLES / "actor-framework/Core/ActorFramework"


def test_a_listed_method_is_owned_when_its_class_is_loaded_for_fields_only() -> (
    None
):
    """describe loads ``GET_AntsLine.vi``'s class for its fields only; the class
    still lists the VI, so the VI is its member (``owns``), it claims the
    class back, and the ``## Class`` section shows."""
    vi = (_ANTS / "GET/GET_AntsLine.vi").resolve()
    cls = str((_ANTS / "Ants.lvclass").resolve())
    graph, key = load_corpus_vi(vi)
    assert graph.owning_class_key(key) == cls
    assert graph._dep_graph.edges[cls, key]["rel"] == EdgeRel.OWNS
    assert graph.claimed_owner(key) == cls
    assert graph.problems([key]) == []
    text = describe_vi(graph, key)
    assert "## Class\n  Ants.lvclass\n" in text
    assert "    GET_AntsLine.vi\n" in text


def test_a_child_class_lists_its_overrides_and_inherited_methods() -> None:
    """``Batch Msg.lvclass`` lists ``Do.vi`` (dynamic, as ``Message``'s is) and
    ``Send Batch.vi``; ``Message`` adds its protected ``Drop Message Core.vi``.
    Its community ``Drop Message.vi`` is not seen by a child."""
    graph = InMemoryVIGraph()
    key = graph.load_lvclass(_AF / "Batch Msg/Batch Msg.lvclass", LoadMode.MINIMAL)
    message = str((_AF / "Message/Message.lvclass").resolve())
    assert [(m.name, m.origin, m.ancestor) for m in graph.class_methods(key)] == [
        ("Do.vi", MethodOrigin.OVERRIDE, message),
        ("Send Batch.vi", MethodOrigin.OWN, None),
        ("Drop Message Core.vi", MethodOrigin.INHERITED, message),
    ]


def test_a_class_is_named_by_the_library_chain_its_own_file_records() -> None:
    """``Message.lvclass`` records ``NI.Lib.ContainingLib`` = ``Actor
    Framework.lvlib``: it is named so when loaded on its own, with no caller
    and no library loaded."""
    graph = InMemoryVIGraph()
    key = graph.load_lvclass(_AF / "Message/Message.lvclass", LoadMode.MINIMAL)
    assert graph.vi_display_name(key) == "Actor Framework.lvlib:Message.lvclass"
