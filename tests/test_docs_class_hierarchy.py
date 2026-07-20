"""Tests for class-hierarchy graph queries and the docs class landing page.

Covers task #47: class landing pages + bidirectional class/method hierarchy
navigation in the HTML documentation generator.

Most tests build a synthetic ``InMemoryVIGraph`` directly (matching the
pattern in ``test_vi_graph.py``) so they don't depend on any local sample
data. A couple of end-to-end tests use the real JKI-VI-Tester sample and are
skipped when it isn't present on disk (consistent with other sample-backed
tests in this repo).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.docs.html_generator import HTMLDocGenerator
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.graph.models import (
    ClassFieldEntry,
    ClassHierarchyInfo,
    MethodAccessInfo,
    MethodOverrideInfo,
)
from lvkit.models import ClusterField, LVType

SAMPLE_ROOT = Path(".lvkit/cache/samples/JKI-VI-Tester/source")


def _has_sample() -> bool:
    return SAMPLE_ROOT.exists()


# ---------------------------------------------------------------------------
# Synthetic graph builder
# ---------------------------------------------------------------------------


def _add_class(
    graph: InMemoryVIGraph,
    classname: str,
    *,
    parent_class: str | None = None,
    fields: list[ClusterField] | None = None,
) -> None:
    graph._dep_graph.add_node(
        classname,
        node_type="class",
        fields=fields or [],
        parent_class=parent_class,
    )


def _add_method(
    graph: InMemoryVIGraph,
    classname: str,
    method_name: str,
    *,
    scope: str = "public",
    is_accessor: bool = False,
    accessor_type: str | None = None,
    accessor_field: str | None = None,
    documented: bool = True,
) -> str:
    """Add an "owns" edge from ``classname`` to a method VI, optionally
    registering the method VI as a documented VI (in list_vis())."""
    vi_name = f"{classname}:{method_name}"
    if documented:
        graph._vi_nodes[vi_name] = set()
    graph._dep_graph.add_node(vi_name)
    graph._dep_graph.add_edge(
        classname,
        vi_name,
        rel="owns",
        scope=scope,
        is_accessor=is_accessor,
        accessor_type=accessor_type,
        accessor_field=accessor_field,
    )
    return vi_name


@pytest.fixture
def class_graph() -> InMemoryVIGraph:
    """Base.lvclass <- Mid.lvclass <- Leaf.lvclass, each with a "run" method.

    Base and Mid both define "run.vi"; Leaf does not (Leaf.run.vi is
    intentionally absent so override-chain tests can check the "not
    documented" skip path). Mid also has a private accessor.
    """
    g = InMemoryVIGraph()

    counter_type = LVType(kind="primitive", underlying_type="I32")
    label_type = LVType(kind="primitive", underlying_type="String")
    _add_class(
        g, "Base.lvclass",
        fields=[ClusterField(name="counter", type=counter_type)],
    )
    _add_class(
        g, "Mid.lvclass", parent_class="Base",
        fields=[ClusterField(name="label", type=label_type)],
    )
    _add_class(g, "Leaf.lvclass", parent_class="Mid", fields=[])

    _add_method(g, "Base.lvclass", "run.vi", scope="public")
    _add_method(g, "Mid.lvclass", "run.vi", scope="public")
    # Leaf deliberately has no "run.vi" — overridden_by should skip it.
    _add_method(g, "Mid.lvclass", "Read label.vi", scope="private",
                is_accessor=True, accessor_type="getter", accessor_field="label")
    _add_method(g, "Leaf.lvclass", "onlyOnLeaf.vi", scope="protected")
    # An undocumented method VI on Base (not in list_vis()) — must not
    # appear in ClassHierarchyInfo.methods.
    _add_method(g, "Base.lvclass", "hidden.vi", documented=False)

    return g


class TestListClasses:
    def test_lists_only_class_nodes(self, class_graph: InMemoryVIGraph):
        assert class_graph.list_classes() == [
            "Base.lvclass", "Leaf.lvclass", "Mid.lvclass",
        ]

    def test_excludes_stub_classes(self, class_graph: InMemoryVIGraph):
        class_graph._dep_graph.add_node("Stub.lvclass", node_type="class")
        class_graph._stubs.add("Stub.lvclass")
        assert "Stub.lvclass" not in class_graph.list_classes()


class TestClassHierarchy:
    def test_root_class_has_no_parent(self, class_graph: InMemoryVIGraph):
        info = class_graph.get_class_hierarchy("Base.lvclass")
        assert info is not None
        assert info.parent_class is None
        assert info.child_classes == ["Mid.lvclass"]

    def test_middle_class_has_parent_and_child(self, class_graph: InMemoryVIGraph):
        info = class_graph.get_class_hierarchy("Mid.lvclass")
        assert info is not None
        assert info.parent_class == "Base.lvclass"
        assert info.child_classes == ["Leaf.lvclass"]

    def test_leaf_class_has_parent_no_children(self, class_graph: InMemoryVIGraph):
        info = class_graph.get_class_hierarchy("Leaf.lvclass")
        assert info is not None
        assert info.parent_class == "Mid.lvclass"
        assert info.child_classes == []

    def test_methods_only_include_documented_vis(self, class_graph: InMemoryVIGraph):
        info = class_graph.get_class_hierarchy("Base.lvclass")
        assert info is not None
        # "hidden.vi" was never registered in list_vis() -> excluded.
        assert info.methods == ["Base.lvclass:run.vi"]

    def test_fields_mark_inherited_vs_own(self, class_graph: InMemoryVIGraph):
        info = class_graph.get_class_hierarchy("Mid.lvclass")
        assert info is not None
        counter_type = LVType(kind="primitive", underlying_type="I32")
        label_type = LVType(kind="primitive", underlying_type="String")
        assert info.fields == [
            ClassFieldEntry(
                field=ClusterField(name="counter", type=counter_type),
                inherited=True,
            ),
            ClassFieldEntry(
                field=ClusterField(name="label", type=label_type),
                inherited=False,
            ),
        ]

    def test_unloaded_class_returns_none(self, class_graph: InMemoryVIGraph):
        assert class_graph.get_class_hierarchy("Nonexistent.lvclass") is None

    def test_parent_not_loaded_is_omitted(self):
        """parent_class is only surfaced if the parent is itself a loaded
        (non-stub) class node — avoids dangling links on partial loads."""
        g = InMemoryVIGraph()
        _add_class(g, "Orphan.lvclass", parent_class="NeverLoaded")
        info = g.get_class_hierarchy("Orphan.lvclass")
        assert info is not None
        assert info.parent_class is None


class TestOwningClassAndAccess:
    def test_get_owning_class(self, class_graph: InMemoryVIGraph):
        assert class_graph.get_owning_class("Mid.lvclass:run.vi") == "Mid.lvclass"

    def test_get_owning_class_none_for_non_method(self, class_graph: InMemoryVIGraph):
        assert class_graph.get_owning_class("SomeOther.vi") is None

    def test_get_method_access_public(self, class_graph: InMemoryVIGraph):
        access = class_graph.get_method_access("Base.lvclass:run.vi")
        assert access == MethodAccessInfo(
            vi_name="Base.lvclass:run.vi",
            scope="public",
            is_accessor=False,
            accessor_type=None,
            accessor_field=None,
        )

    def test_get_method_access_accessor(self, class_graph: InMemoryVIGraph):
        access = class_graph.get_method_access("Mid.lvclass:Read label.vi")
        assert access is not None
        assert access.scope == "private"
        assert access.is_accessor is True
        assert access.accessor_type == "getter"
        assert access.accessor_field == "label"

    def test_get_method_access_none_for_non_method(self, class_graph: InMemoryVIGraph):
        assert class_graph.get_method_access("SomeOther.vi") is None


class TestMethodOverrides:
    def test_parent_and_child_overrides(self):
        """A method defined on Base, Mid, and Leaf links up (overrides) and
        down (overridden_by) by bare method name at each level."""
        g = InMemoryVIGraph()
        _add_class(g, "Base.lvclass")
        _add_class(g, "Mid.lvclass", parent_class="Base")
        _add_class(g, "Leaf.lvclass", parent_class="Mid")
        _add_method(g, "Base.lvclass", "run.vi")
        _add_method(g, "Mid.lvclass", "run.vi")
        _add_method(g, "Leaf.lvclass", "run.vi")

        mid_overrides = g.get_method_overrides("Mid.lvclass:run.vi")
        assert mid_overrides == MethodOverrideInfo(
            vi_name="Mid.lvclass:run.vi",
            overrides="Base.lvclass:run.vi",
            overridden_by=["Leaf.lvclass:run.vi"],
        )

        base_overrides = g.get_method_overrides("Base.lvclass:run.vi")
        assert base_overrides is not None
        assert base_overrides.overrides is None
        assert base_overrides.overridden_by == ["Mid.lvclass:run.vi"]

        leaf_overrides = g.get_method_overrides("Leaf.lvclass:run.vi")
        assert leaf_overrides is not None
        assert leaf_overrides.overrides == "Mid.lvclass:run.vi"
        assert leaf_overrides.overridden_by == []

    def test_undocumented_target_is_skipped(self):
        """A same-named method on the parent exists in the dep_graph (e.g.
        it failed to convert) but isn't in list_vis() -> not linked."""
        g = InMemoryVIGraph()
        _add_class(g, "Base.lvclass")
        _add_class(g, "Mid.lvclass", parent_class="Base")
        _add_method(g, "Base.lvclass", "run.vi", documented=False)
        _add_method(g, "Mid.lvclass", "run.vi")

        overrides = g.get_method_overrides("Mid.lvclass:run.vi")
        assert overrides is None

    def test_bare_name_match_ignores_class_prefix(self):
        """Override matching is by bare method name only, not by any
        string relationship between the VI names themselves."""
        g = InMemoryVIGraph()
        _add_class(g, "Alpha.lvclass")
        _add_class(g, "Beta.lvclass", parent_class="Alpha")
        _add_method(g, "Alpha.lvclass", "shared name with spaces.vi")
        _add_method(g, "Beta.lvclass", "shared name with spaces.vi")

        overrides = g.get_method_overrides("Beta.lvclass:shared name with spaces.vi")
        assert overrides is not None
        assert overrides.overrides == "Alpha.lvclass:shared name with spaces.vi"

    def test_no_override_relationship_returns_none(self, class_graph: InMemoryVIGraph):
        # onlyOnLeaf.vi has no same-named method on Mid or any subclass.
        assert class_graph.get_method_overrides("Leaf.lvclass:onlyOnLeaf.vi") is None

    def test_none_for_non_method_vi(self, class_graph: InMemoryVIGraph):
        assert class_graph.get_method_overrides("NotAMethod.vi") is None


# ---------------------------------------------------------------------------
# HTML rendering: filename collision avoidance + rendered content
# ---------------------------------------------------------------------------


class TestClassPageFilenames:
    def test_class_page_distinct_from_method_pages(self, tmp_path: Path):
        gen = HTMLDocGenerator(tmp_path, "Docs", "directory")
        class_file = gen._class_name_to_filename("TestCase.lvclass")
        method_file = gen._vi_name_to_filename("TestCase.lvclass:method.vi")
        assert class_file != method_file
        # Same subdirectory (intra-class links need no "../" prefix).
        assert class_file.rsplit("/", 1)[0] == method_file.rsplit("/", 1)[0]

    def test_no_collision_across_many_method_names(self, tmp_path: Path):
        gen = HTMLDocGenerator(tmp_path, "Docs", "directory")
        classname = "TestCase.lvclass"
        class_file = gen._class_name_to_filename(classname)
        method_names = ["run.vi", "class.vi", "TestCase.vi", "a b c.vi"]
        method_files = {
            gen._vi_name_to_filename(f"{classname}:{m}") for m in method_names
        }
        assert class_file not in method_files


class TestClassPageGeneration:
    def test_generate_class_page_writes_expected_sections(self, tmp_path: Path):
        gen = HTMLDocGenerator(tmp_path, "Docs", "directory")
        hierarchy = ClassHierarchyInfo(
            classname="Mid.lvclass",
            parent_class="Base.lvclass",
            child_classes=["Leaf.lvclass"],
            methods=["Mid.lvclass:run.vi"],
            fields=[
                ClassFieldEntry(
                    field=ClusterField(
                        name="x", type=LVType(kind="primitive", underlying_type="I32")
                    ),
                    inherited=False,
                ),
            ],
        )
        access = {
            "Mid.lvclass:run.vi": MethodAccessInfo(
                vi_name="Mid.lvclass:run.vi",
                scope="protected",
                is_accessor=False,
                accessor_type=None,
                accessor_field=None,
            ),
        }
        gen.generate_class_page(hierarchy, access)

        filename = gen._class_name_to_filename("Mid.lvclass")
        html = (tmp_path / filename).read_text(encoding="utf-8")

        assert "Base.lvclass" in html  # Inherits from
        assert "Leaf.lvclass" in html  # Subclasses
        assert "run.vi" in html
        assert "scope-protected" in html
        assert gen.class_pages["Mid.lvclass"] == filename

    def test_index_page_links_class_group_header(self, tmp_path: Path):
        gen = HTMLDocGenerator(tmp_path, "Docs", "directory")
        hierarchy = ClassHierarchyInfo(
            classname="Mid.lvclass",
            parent_class=None,
            child_classes=[],
            methods=["Mid.lvclass:run.vi"],
            fields=[],
        )
        gen.generate_class_page(hierarchy, {})
        gen.all_vis = {"Mid.lvclass:run.vi"}
        index_html = gen._render_index_page(["Mid.lvclass:run.vi"])

        expected_href = gen.class_pages["Mid.lvclass"]
        assert f'<a href="{expected_href}">Mid.lvclass</a>' in index_html


# ---------------------------------------------------------------------------
# Real-sample end-to-end tests (skipped if the sample isn't present locally)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _has_sample(), reason="JKI-VI-Tester sample not present")
class TestRealSampleHierarchy:
    def test_testrunner_texttestrunner_hierarchy(self):
        g = InMemoryVIGraph()
        g.load_lvclass(
            SAMPLE_ROOT / "Classes/TestRunner/TestRunner.lvclass",
            mode=LoadMode.FULL,
        )
        g.load_lvclass(
            SAMPLE_ROOT / "Classes/TextTestRunner/TextTestRunner.lvclass",
            mode=LoadMode.FULL,
        )

        parent_hierarchy = g.get_class_hierarchy("TestRunner.lvclass")
        assert parent_hierarchy is not None
        assert "TextTestRunner.lvclass" in parent_hierarchy.child_classes

        child_hierarchy = g.get_class_hierarchy("TextTestRunner.lvclass")
        assert child_hierarchy is not None
        assert child_hierarchy.parent_class == "TestRunner.lvclass"

        overrides = g.get_method_overrides("TextTestRunner.lvclass:makeResult.vi")
        assert overrides is not None
        assert overrides.overrides == "TestRunner.lvclass:makeResult.vi"

        reverse = g.get_method_overrides("TestRunner.lvclass:makeResult.vi")
        assert reverse is not None
        assert "TextTestRunner.lvclass:makeResult.vi" in reverse.overridden_by
