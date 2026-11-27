"""A VI saved inside its caller's file has no file of its own: its path is its
caller's path and its name, and what its own link table records relative to it
resolves from there."""

from __future__ import annotations

import networkx as nx

from lvkit.graph.recorded_paths import RecordedPaths

CALLER = "/proj/caller.vi"
INSTANCE = f"{CALLER}/0123-guid.vi"
CALLER_PATH = ["<vilib>", "Utility", "caller.vi"]


def _paths() -> RecordedPaths:
    graph = nx.DiGraph()
    graph.add_node(CALLER)
    graph.add_node(INSTANCE, embedded_in=CALLER)
    graph.add_node("/proj/helper.vi")
    graph.add_edge(CALLER, INSTANCE)
    paths = RecordedPaths(graph)
    paths.record(CALLER, None, CALLER_PATH)
    paths.owners_changed()
    return paths


def test_instance_path_is_its_callers_path_and_its_name() -> None:
    paths = _paths()

    assert paths.resolved(INSTANCE) == [*CALLER_PATH, "0123-guid.vi"]


def test_a_path_the_instance_records_relative_to_itself_leaves_through_its_caller() -> (
    None
):
    paths = _paths()
    # The instance sits one level below its caller: its first empty token pops its
    # own name, its second the caller's.
    paths.record("/proj/helper.vi", INSTANCE, ["", "", "helper.vi"])

    assert paths.path("/proj/helper.vi") == "<vilib>/Utility/helper.vi"


def test_instance_of_a_caller_with_no_installation_path_has_none() -> None:
    graph = nx.DiGraph()
    graph.add_node(CALLER)
    graph.add_node(INSTANCE, embedded_in=CALLER)
    paths = RecordedPaths(graph)

    assert paths.resolved(INSTANCE) is None
    assert paths.resolved(CALLER) is None
