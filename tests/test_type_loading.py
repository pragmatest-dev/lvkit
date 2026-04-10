"""Tests for type dependency loading (graph/loading.py).

Covers _load_type_dependencies, _ensure_type_loaded, _ensure_typedef_loaded,
_find_file — the paths that resolve class/typedef references in a VI's type_map.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvpy.memory_graph import InMemoryVIGraph

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
TESTCASE_DIR = SAMPLES / "JKI-VI-Tester" / "source" / "Classes" / "TestCase"
TESTRESULT_DIR = SAMPLES / "JKI-VI-Tester" / "source" / "Classes" / "TestResult"
ADDERROR_VI = TESTRESULT_DIR / "addError.vi"
RUN_VI = TESTCASE_DIR / "run.vi"
TESTCASE_LVCLASS = TESTCASE_DIR / "TestCase.lvclass"
SEARCH_PATHS = [
    SAMPLES / "JKI-VI-Tester" / "source" / "Classes",
]


@pytest.mark.skipif(
    not ADDERROR_VI.exists(),
    reason="JKI-VI-Tester samples not available",
)
class TestLoadVIWithTypeDependencies:
    """Loading a VI should pull class/typedef deps into dep_graph."""

    def test_class_dependency_loaded(self):
        """addError.vi references TestResult.lvclass — it should appear in dep_graph."""
        graph = InMemoryVIGraph()
        graph.load_vi(ADDERROR_VI, expand_subvis=True, search_paths=SEARCH_PATHS)

        # TestResult.lvclass should be in dep_graph as a class node
        # (not just as part of the VI name "TestResult.lvclass:addError.vi")
        class_nodes = [
            n for n in graph._dep_graph.nodes
            if n.endswith("TestResult.lvclass")
        ]
        assert len(class_nodes) >= 1, (
            "TestResult.lvclass not in dep_graph. "
            f"Nodes: {list(graph._dep_graph.nodes)}"
        )

    def test_typedef_dependency_loaded(self):
        """addError.vi references .ctl typedefs — they should be in dep_graph."""
        graph = InMemoryVIGraph()
        graph.load_vi(ADDERROR_VI, expand_subvis=True, search_paths=SEARCH_PATHS)

        all_nodes = list(graph._dep_graph.nodes)
        ctl_refs = [n for n in all_nodes if ".ctl" in n]
        # Typedefs should be tracked (loaded or stubbed)
        assert len(ctl_refs) >= 1, (
            f"No .ctl references in dep_graph. Nodes: {all_nodes}"
        )


class TestEnsureTypeLoaded:
    """_ensure_type_loaded: load or stub a class dependency."""

    def test_stub_for_missing_class(self):
        graph = InMemoryVIGraph()
        graph._ensure_type_loaded(
            "NonExistent.lvclass",
            search_paths=[],
            caller_dir=Path("/nonexistent"),
        )
        assert graph._dep_graph.has_node("NonExistent.lvclass")
        assert "NonExistent.lvclass" in graph._stubs

    def test_already_loaded_is_skipped(self):
        graph = InMemoryVIGraph()
        graph._dep_graph.add_node("Already.lvclass", node_type="class")
        initial_count = graph._dep_graph.number_of_nodes()

        graph._ensure_type_loaded(
            "Already.lvclass",
            search_paths=[],
            caller_dir=Path("/nonexistent"),
        )
        assert graph._dep_graph.number_of_nodes() == initial_count

    def test_qualified_name_extracts_leaf(self):
        """Qualified name extracts leaf for file search."""
        graph = InMemoryVIGraph()
        graph._ensure_type_loaded(
            "SomeLib.lvlib:Missing.lvclass",
            search_paths=[],
            caller_dir=Path("/nonexistent"),
        )
        # Should be stubbed under the full qualified name
        assert graph._dep_graph.has_node("SomeLib.lvlib:Missing.lvclass")
        assert "SomeLib.lvlib:Missing.lvclass" in graph._stubs


class TestEnsureTypedefLoaded:
    """_ensure_typedef_loaded: load or stub a typedef dependency."""

    def test_stub_for_missing_typedef(self):
        graph = InMemoryVIGraph()
        graph._ensure_typedef_loaded(
            "Missing.ctl",
            search_paths=[],
            caller_dir=Path("/nonexistent"),
        )
        assert graph._dep_graph.has_node("Missing.ctl")
        assert "Missing.ctl" in graph._stubs

    def test_already_loaded_is_skipped(self):
        graph = InMemoryVIGraph()
        graph._dep_graph.add_node("Known.ctl", node_type="typedef")
        initial_count = graph._dep_graph.number_of_nodes()

        graph._ensure_typedef_loaded(
            "Known.ctl",
            search_paths=[],
            caller_dir=Path("/nonexistent"),
        )
        assert graph._dep_graph.number_of_nodes() == initial_count


class TestFindFile:
    """_find_file: search for a file by name in caller_dir + search_paths."""

    def test_caller_dir_priority(self, tmp_path):
        caller_dir = tmp_path / "caller"
        caller_dir.mkdir()
        search_dir = tmp_path / "search"
        search_dir.mkdir()

        # File in both locations
        (caller_dir / "target.ctl").write_text("caller")
        (search_dir / "target.ctl").write_text("search")

        graph = InMemoryVIGraph()
        result = graph._find_file("target.ctl", [search_dir], caller_dir)
        assert result is not None
        assert result.parent == caller_dir

    def test_search_path_rglob(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        (nested / "deep.ctl").write_text("found")

        graph = InMemoryVIGraph()
        result = graph._find_file("deep.ctl", [tmp_path], Path("/nonexistent"))
        assert result is not None
        assert result.name == "deep.ctl"

    def test_not_found_returns_none(self, tmp_path):
        graph = InMemoryVIGraph()
        result = graph._find_file("nowhere.ctl", [tmp_path], tmp_path)
        assert result is None
