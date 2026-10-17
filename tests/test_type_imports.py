"""The classes and typedefs a generated module names are defined and imported.

An annotation names a LabVIEW class or typedef by the class generated for it, and
an enum constant is a member of the enum class generated for its typedef. The
module imports each such class from the module that defines it, found by the
caller's own dependencies: under ``TYPE_CHECKING`` for annotations (no import
cycle at run time), at run time for the values built from a class. A class or
typedef the graph did not load has no class to import, so it is annotated by its
LabVIEW representation instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.context import CodeGenContext
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import CaseStructureNode, PrimitiveNode
from lvkit.graph.node_kinds import NodeType
from lvkit.models import (
    ClusterField,
    EnumValue,
    LVType,
    LVTypeKind,
    Terminal,
    TunnelTerminal,
)
from lvkit.pipeline import create_import_resolver
from lvkit.type_names import ROOT_CLASS, derive_python_name, is_enum_member_name
from tests.helpers import make_graph_with_edge


def _enum(typedef_name: str | None, **members: int) -> LVType:
    return LVType(
        kind=LVTypeKind.ENUM,
        typedef_name=typedef_name,
        values={name: EnumValue(value) for name, value in members.items()},
    )


def _class_refnum(classname: str) -> LVType:
    return LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum", classname=classname
    )


class TestClassNames:
    @pytest.mark.parametrize(
        ("file_name", "class_name"),
        [
            ("Method--enum.ctl", "MethodEnum"),
            ("method--Enum.ctl", "MethodEnum"),
            ("methodEnum.ctl", "MethodEnum"),
            ("sysdir.llb:System Directory Type.ctl", "SystemDirectoryType"),
            ("TestCase.lvclass", "TestCase"),
            ("Lib.lvlib:Foo Bar.ctl", "FooBar"),
            ("Type Descriptor__ogtk.ctl", "TypeDescriptorOgtk"),
            (ROOT_CLASS, "LabVIEWObject"),
            ("3D Point.ctl", "_3DPoint"),
        ],
    )
    def test_derive_python_name(self, file_name: str, class_name: str):
        assert derive_python_name(file_name) == class_name

    def test_an_enum_item_must_be_an_attribute_name(self):
        assert is_enum_member_name("setUp")
        assert not is_enum_member_name("<Null>")
        assert not is_enum_member_name("None")

    def test_annotation_and_constant_spell_the_same_class(self):
        lv_type = _enum("TestCase.lvclass:method--Enum.ctl", setUp=0, tearDown=1)
        assert lv_type.to_python() == "MethodEnum"
        assert lv_type.enum_member_ref(1) == "MethodEnum.tearDown"


class TestUnloadedTypes:
    def test_named_types_of_an_array_of_a_class(self):
        element = _class_refnum("TestCase.lvclass")
        array = LVType(kind=LVTypeKind.ARRAY, element_type=element, dimensions=1)
        assert [t.type_ref() for t in array.named_types()] == ["TestCase.lvclass"]
        assert array.to_python() == "list[TestCase]"

    def test_a_type_without_an_importable_class_is_its_representation(self):
        never = lambda _t: False  # noqa: E731
        assert _enum("M.ctl", a=0).to_python(never) == "int"
        assert _class_refnum("TestCase.lvclass").to_python(never) == "Any"
        array = LVType(
            kind=LVTypeKind.ARRAY,
            element_type=_class_refnum("TestCase.lvclass"),
            dimensions=1,
        )
        assert array.to_python(never) == "list[Any]"

    def test_an_enum_constant_of_an_unimportable_class_is_not_a_member_ref(self):
        lv_type = _enum("M.ctl", a=0)
        assert lv_type.enum_member_ref(0, lambda _t: False) is None
        assert lv_type.enum_member_ref(0, lambda _t: True) == "M.a"

    def test_an_item_that_is_a_keyword_has_no_member_ref(self):
        assert _enum("M.ctl", **{"None": 0}).enum_member_ref(0) is None


class TestImportDepth:
    """Every import is relative to the package directory of the module that holds
    it, whatever library folders the two modules sit in."""

    @staticmethod
    def _resolver(tmp_path: Path, caller: str):
        out = tmp_path / "pkg"
        paths = {
            "Root.vi": out / "root_vi.py",
            "A.lvlib:Dep.vi": out / "lib_a" / "dep_vi.py",
            "B.lvlib:Dep2.vi": out / "lib_b" / "dep2_vi.py",
            "Deep.vi": out / "lib_a" / "inner" / "deep_vi.py",
        }
        return create_import_resolver("pkg", out, paths, out / caller)

    def test_root_module_imports_into_a_library_folder(self, tmp_path: Path):
        resolver = self._resolver(tmp_path, "root_vi.py")
        assert resolver("A.lvlib:Dep.vi") == "from .lib_a.dep_vi import dep"

    def test_library_module_imports_from_the_root(self, tmp_path: Path):
        resolver = self._resolver(tmp_path, "lib_a/dep_vi.py")
        assert resolver("Root.vi") == "from ..root_vi import root"

    def test_library_module_imports_into_another_library(self, tmp_path: Path):
        resolver = self._resolver(tmp_path, "lib_a/dep_vi.py")
        assert resolver("B.lvlib:Dep2.vi") == "from ..lib_b.dep2_vi import dep2"

    def test_nested_module_imports_a_module_of_its_parent_folder(self, tmp_path: Path):
        resolver = self._resolver(tmp_path, "lib_a/inner/deep_vi.py")
        assert resolver("A.lvlib:Dep.vi") == "from ...lib_a.dep_vi import dep"

    def test_nested_module_reaches_the_root_with_one_dot_per_level(
        self, tmp_path: Path
    ):
        resolver = self._resolver(tmp_path, "lib_a/inner/deep_vi.py")
        assert resolver("Root.vi") == "from ...root_vi import root"

    def test_a_whole_module_import_has_the_same_depth(self, tmp_path: Path):
        in_library = self._resolver(tmp_path, "lib_a/dep_vi.py")
        assert in_library.module("Root.vi").statement == (
            "from .. import root_vi as root_vi_global"
        )
        at_root = self._resolver(tmp_path, "root_vi.py")
        assert at_root.module("A.lvlib:Dep.vi").statement == (
            "from .lib_a import dep_vi as dep_vi_global"
        )

    def test_the_root_class_is_a_runtime_module(self, tmp_path: Path):
        resolver = self._resolver(tmp_path, "root_vi.py")
        assert resolver.type_import(ROOT_CLASS) == (
            "from lvkit.runtime.labview_object import LabVIEWObject"
        )

    def test_a_type_with_no_graph_is_not_importable(self, tmp_path: Path):
        resolver = self._resolver(tmp_path, "root_vi.py")
        assert resolver.type_import("TestCase.lvclass") is None


class TestErrorClusterTunnels:
    """An error is an exception, so a structure tunnel or shift register that
    carries an error cluster has no value to bind, merge or carry."""

    @staticmethod
    def _pair(
        outer: str, inner: str, lv_type: LVType, name: str
    ) -> list[TunnelTerminal]:
        common = dict(tunnel_type="lpTun", lv_type=lv_type, name=name)
        return [
            TunnelTerminal(
                id=outer,
                index=0,
                direction="output",
                boundary="outer",
                paired_id=inner,
                **common,
            ),
            TunnelTerminal(
                id=inner,
                index=1,
                direction="input",
                boundary="inner",
                paired_id=outer,
                **common,
            ),
        ]

    def test_tunnels_skip_the_error_cluster(self):
        error = LVType(
            kind=LVTypeKind.CLUSTER,
            fields=[
                ClusterField(name="status"),
                ClusterField(name="code"),
                ClusterField(name="source"),
            ],
        )
        number = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")
        node = CaseStructureNode(
            id="case",
            vi_path="test.vi",
            terminals=[
                *self._pair("o_value", "i_value", number, "value"),
                *self._pair("o_error", "i_error", error, "error out"),
            ],
        )
        tunnels = CodeGenContext.tunnels(node)
        assert [t.outer_terminal_uid for t in tunnels] == ["o_value"]


class TestResolveStaysInTheVI:
    """A subVI call is wired to the called VI's own terminals; the names bound to
    those belong to the called VI, not to the caller."""

    def test_a_callers_terminal_never_resolves_to_the_called_vis_name(self):
        graph = make_graph_with_edge("callee_out", "caller_in")
        graph._graph.nodes["p1"]["node"] = PrimitiveNode(
            id="p1",
            vi_path="callee.vi",
            name="p1",
            terminals=[Terminal(id="callee_out", index=0, direction="output")],
        )
        ctx = CodeGenContext(graph=graph)
        ctx.bind("callee_out", "the_callees_variable")
        assert ctx.resolve("caller_in") is None
        ctx.bind("caller_in", "mine")
        assert ctx.resolve("caller_in") == "mine"


class TestTypeReferenceResolution:
    """A class or typedef a VI names is the one of its own dependencies with that
    name, even where the graph also holds a placeholder keyed by the bare name."""

    @staticmethod
    def _graph() -> InMemoryVIGraph:
        graph = InMemoryVIGraph()
        deps = graph._dep_graph
        deps.add_node("/p/caller.vi")
        deps.add_node(
            "/p/Foo.lvclass",
            node_type=NodeType.CLASS,
            qname="Lib.lvlib:Foo.lvclass",
            name="Foo.lvclass",
            fields=[ClusterField(name="a")],
        )
        deps.add_edge("/p/caller.vi", "/p/Foo.lvclass")
        deps.add_node("Lib.lvlib:Foo.lvclass", node_type=NodeType.CLASS)
        graph._stubs.add("Lib.lvlib:Foo.lvclass")
        return graph

    def test_the_class_is_the_callers_dependency_not_the_name_placeholder(self):
        graph = self._graph()
        key = graph.type_key("Lib.lvlib:Foo.lvclass", "/p/caller.vi")
        assert key == "/p/Foo.lvclass"

    def test_class_fields_come_from_the_callers_dependency(self):
        graph = self._graph()
        fields = graph.get_class_fields("Lib.lvlib:Foo.lvclass", "/p/caller.vi")
        assert [f.name for f in fields or []] == ["a"]

    def test_a_name_no_dependency_carries_is_not_a_type(self):
        graph = self._graph()
        assert graph.type_key("Other.lvclass", "/p/caller.vi") is None
