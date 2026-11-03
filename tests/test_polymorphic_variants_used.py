"""A call of a polymorphic VI calls the variant its caller records, from the
polymorphic VI's module, which holds only its variants. A variant gets
generated code when a VI that is itself generated uses it: a call of the wrapper
that records it, a call of it by name from another variant, or a reference to
it. A call that records no variant is a gap."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lvkit import cli, pipeline
from lvkit.extract_pool import ExtractionPool
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import GraphNode, PolyInfo, VIContext, VINode
from lvkit.load_mode import LoadMode
from lvkit.models import LVType, LVTypeKind
from lvkit.pipeline import generate_python
from lvkit.polymorphic_variants import variants_to_generate
from tests.class_graphs import INT, Project, Recipe, add, call, pane, wire
from tests.corpus_graphs import OPENG_USERLIB
from tests.test_call_identity import _Call, _record, _run, _static, _Tree
from tests.test_run_vi import runner


class _Graph:
    """A graph of the keys named in ``dependents`` (key to the VIs that use it)."""

    def __init__(
        self,
        calls: dict[str, dict[str, set[str | None]]],
        dependents: dict[str, list[str]],
    ) -> None:
        self._calls = calls
        self._dependents = dependents

    def poly_call_choices(self) -> dict[str, dict[str, set[str | None]]]:
        return self._calls

    def get_vi_dependents(self, key: str) -> list[str]:
        return self._dependents.get(key, [])

    def recorded_path(self, key: str) -> str:
        return key


class _Resolver:
    def has_python(self, path: str | None) -> bool:
        return path == "catalog"


def _chosen(
    graph: _Graph, written: set[str], variants: list[str] | None = None
) -> list[str]:
    groups = {"wrap": variants or ["catalog", "array", "scalar"]}
    return variants_to_generate(
        graph,  # type: ignore[arg-type]
        _Resolver(),  # type: ignore[arg-type]
        groups,
        written,
    )["wrap"]


def test_a_variant_a_chosen_variant_calls_is_generated() -> None:
    graph = _Graph({"wrap": {"main": {"array"}}}, {"scalar": ["array", "wrap"]})
    assert _chosen(graph, {"main"}) == ["array", "scalar"]


def test_a_variant_only_a_variant_nothing_uses_calls_is_left_out() -> None:
    graph = _Graph({"wrap": {"main": {"scalar"}}}, {"scalar": ["wrap"], "array": []})
    assert _chosen(graph, {"main"}) == ["scalar"]


def test_a_variant_a_generated_vi_references_is_generated() -> None:
    graph = _Graph({"wrap": {"main": {"array"}}}, {"scalar": ["main"]})
    assert _chosen(graph, {"main"}) == ["array", "scalar"]


def test_a_variant_used_by_a_vi_that_is_not_generated_is_left_out() -> None:
    graph = _Graph({"wrap": {"main": {"array"}}}, {"scalar": ["elsewhere"]})
    assert _chosen(graph, {"main"}) == ["array"]


def test_a_call_that_records_no_variant_needs_none() -> None:
    graph = _Graph({"wrap": {"main": {None}}}, {})
    assert _chosen(graph, {"main"}) == []


def test_a_wrapper_nothing_calls_keeps_every_variant() -> None:
    assert _chosen(_Graph({}, {}), set()) == ["array", "scalar"]


def test_a_catalog_variant_is_never_generated() -> None:
    graph = _Graph({"wrap": {"main": {"catalog"}}}, {})
    assert _chosen(graph, {"main"}) == []


_VARIANTS = {"array": ("One", "array.vi"), "scalar": ("Two", "scalar.vi")}


def _poly_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    callers: dict[str, list[str | None]],
) -> _Tree:
    """The wrapper ``wrap.vi`` with the variants ``One/array.vi`` (which calls
    ``Two/scalar.vi`` by name) and ``Two/scalar.vi``; each of ``callers`` (a VI
    file name to the variant each of its calls records, or None for none) calls
    the wrapper."""
    built = _Tree(tmp_path / "src")
    monkeypatch.setattr("lvkit.graph.loading.extract_vi_xml", built.extract)
    monkeypatch.setattr(ExtractionPool, "prefetch", lambda self, paths: None)
    built.vi("Two/scalar.vi", "scalar.vi", [])
    built.vi(
        "One/array.vi",
        "array.vi",
        [],
        [_static(["scalar.vi"], ["", "..", "Two", "scalar.vi"])],
    )
    variants = list(_VARIANTS.values())
    wrapper = built.vi(
        "wrap.vi",
        "wrap.vi",
        [],
        [_static([name], ["", folder, name]) for folder, name in variants],
    )
    slot = built.extract(wrapper)[2]
    records = "".join(
        _record("VIVI", [name], ["", folder, name], 0, 10 + i)
        for i, (folder, name) in enumerate(variants)
    )
    text = slot.read_text().replace("</Section></LIvi>", f"{records}</Section></LIvi>")
    text = text.replace(
        "</VI>",
        '<LVSR><Section><Execution2 AllowPolyTypeAdapt="1"/></Section></LVSR></VI>',
    )
    slot.write_text(text)
    for caller, picks in callers.items():
        calls = []
        for variant in picks:
            call_records = [("PUPV", ["wrap.vi"], ["", "wrap.vi"], 0)]
            if variant is not None:
                folder, name = _VARIANTS[variant]
                call_records.append(("IUVI", [name], ["", folder, name], 0))
            calls.append(
                _Call(node_class="polyIUse", label="wrap.vi", records=call_records)
            )
        built.vi(caller, caller, [], calls)
    return built


@pytest.fixture
def poly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Tree:
    """``main.vi`` calls the wrapper ``wrap.vi``, recording its variant
    ``array``."""
    return _poly_tree(tmp_path, monkeypatch, {"main.vi": ["array"]})


_WRAP_MODULE = [
    "'The variants of the polymorphic VI wrap.vi.'",
    "from __future__ import annotations",
    "from typing import Any, NamedTuple",
    "",
    "",
    "def array():",
    "    scalar()",
    "",
    "def scalar():",
    "    pass",
]


def test_a_call_runs_the_variant_its_caller_records(
    poly: _Tree, tmp_path: Path
) -> None:
    """``main`` imports the variant ``array`` from the polymorphic VI's module and
    calls it; the module holds only the variants, and running ``main`` runs
    ``array`` and, through it, ``scalar``."""
    out = tmp_path / "gen"
    result = generate_python(poly.root / "main.vi", out)
    assert not result["gaps"]
    package = out / "main"
    assert (package / "main.py").read_text().splitlines()[2:] == [
        "from .wrap import array",
        "",
        "def main():",
        "    array()",
    ]
    assert (package / "wrap.py").read_text().splitlines() == _WRAP_MODULE
    main = _run(package, "main")
    ran: list[str] = []
    main.array.__globals__["scalar"] = lambda: ran.append("scalar")
    main.main()
    assert ran == ["scalar"]


def test_two_callers_each_call_the_variant_they_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = _poly_tree(
        tmp_path, monkeypatch, {"main.vi": ["array"], "other.vi": ["scalar"]}
    )
    out = tmp_path / "gen"
    result = generate_python(built.root, out)
    assert not result["gaps"]
    package = out / built.root.name
    calls = {
        caller: (package / f"{caller}.py").read_text().splitlines()[2:]
        for caller in ("main", "other")
    }
    assert calls == {
        "main": ["from .wrap import array", "", "def main():", "    array()"],
        "other": ["from .wrap import scalar", "", "def other():", "    scalar()"],
    }
    _run(package, "main").main()
    _run(package, "other").other()


def test_one_caller_of_two_variants_imports_each_by_its_own_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two functions of one module are two imports, each under its own name."""
    built = _poly_tree(tmp_path, monkeypatch, {"main.vi": ["array", "scalar"]})
    out = tmp_path / "gen"
    result = generate_python(built.root / "main.vi", out)
    assert not result["gaps"]
    package = out / "main"
    lines = (package / "main.py").read_text().splitlines()
    assert lines[2:4] == ["from .wrap import array", "from .wrap import scalar"]
    assert lines[4:] == ["", "def main():", "    array()", "    scalar()"]
    _run(package, "main").main()


_NO_VARIANT = (
    "The call of the polymorphic VI wrap.vi records no variant, and the connector "
    "panes of several of its variants match this call: One/array.vi, Two/scalar.vi"
)


def test_a_call_that_records_no_variant_is_a_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The warning names the polymorphic VI and its variants; the call raises
    that reason when it runs, and the run is Partial."""
    built = _poly_tree(tmp_path, monkeypatch, {"main.vi": [None]})
    out = tmp_path / "gen"
    monkeypatch.setattr(
        sys, "argv", ["lvkit", "generate", str(built.root / "main.vi"), "-o", str(out)]
    )
    assert cli.main() == 3
    captured = capsys.readouterr()
    warnings = [
        line for line in captured.err.splitlines() if line.startswith("warning: ")
    ]
    assert warnings[0] == (
        f"warning: main.vi: generate: wrap.vi: {_NO_VARIANT} [unsupported_node]"
    )
    assert "Partial: 1 VIs generated, 1 with gaps (1 gaps)" in captured.out
    package = out / "main"
    source = (package / "main.py").read_text()
    assert f"raise NotImplementedError('{_NO_VARIANT}\\ngap kind: " in source
    assert not (package / "wrap.py").exists()
    with pytest.raises(NotImplementedError, match="records no variant"):
        _run(package, "main").main()


def test_a_polymorphic_call_resolves_to_the_variant_its_record_names(
    poly: _Tree,
) -> None:
    graph = InMemoryVIGraph()
    graph.load_vi(poly.root / "main.vi", LoadMode.MINIMAL)
    key = lambda rel: str((poly.root / rel).resolve())  # noqa: E731
    assert graph.poly_call_choices() == {
        key("wrap.vi"): {key("main.vi"): {key("One/array.vi")}}
    }


def test_a_variants_module_level_code_is_kept_and_paths_are_project_relative(
    poly: _Tree, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A variant's module-level statements (a stored-state global, Call Library
    Function code) go into the polymorphic module with its function, and the
    module's docstring names the polymorphic VI by its project-relative path."""
    real = pipeline.build_module

    def with_state(*args: object, **kwargs: object) -> str:
        return real(*args, **kwargs) + "\n_lv_state_kept = None\n"  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline, "build_module", with_state)
    out = tmp_path / "gen"
    generate_python(poly.root / "main.vi", out)
    source = next(out.rglob("wrap.py")).read_text()
    assert "_lv_state_kept = None" in source
    assert str(poly.root) not in source
    assert source.startswith("'The variants of the polymorphic VI wrap.vi.'\n")


def _variant(input_name: str, output_name: str) -> Recipe:
    """A variant whose output is its input added to itself."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "in", INT, input_name, 0)],
                outputs=[pane(vi, "output", "out", INT, output_name, 1)],
                data_flow=[
                    wire(vi, "in", "s.x"),
                    wire(vi, "in", "s.y"),
                    wire(vi, "s.sum", "out"),
                ],
            ),
            [add(vi, "s")],
        )

    return build


def test_a_call_passes_its_values_by_the_recorded_variants_pane(
    tmp_path: Path,
) -> None:
    """The variants name their terminals differently (``value``/``doubled``,
    ``values``/``total``) and the call's own terminals differently again: the
    call is the recorded variant's function, its input passed and its output
    read by that variant's names."""
    project = Project(tmp_path)
    scalar = project.add_vi("Scalar", _variant("value", "doubled"))
    array = project.add_vi("Array", _variant("values", "total"))
    wrap = str((tmp_path / "src" / "Wrap.vi").resolve())
    project.graph._poly_info[wrap] = PolyInfo(variant_keys=[scalar, array])  # noqa: SLF001

    def calls_array(vi: str) -> tuple[VIContext, list[GraphNode]]:
        node = call(vi, "w", wrap, "polyIUse", [("x", 0, INT)], [("y", 1, INT)])
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "n", INT, "n", 0)],
                outputs=[pane(vi, "output", "r", INT, "r", 1)],
                data_flow=[wire(vi, "n", "w.x"), wire(vi, "w.y", "r")],
            ),
            [
                node.model_copy(
                    update={"variant_key": array, "qualified_name": "Array.vi"}
                )
            ],
        )

    project.add_vi("Caller", calls_array)
    package = project.generate()
    assert package.gaps == []
    source = package.sources["caller"]
    assert "from .array import array\n" in source
    assert "array_result = array(values=n)" in source
    assert "array_result.total" in source
    with package.use() as module:
        assert module("caller").caller(n=4).r == 8


PATH = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Path")
BOOL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
VOID = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Void")


def _array(element: LVType) -> LVType:
    return LVType(kind=LVTypeKind.ARRAY, element_type=element, dimensions=1)


def _file_exists(path_type: LVType, flag_type: LVType) -> Recipe:
    """A File Exists variant: ``file path`` (2) in, ``is directory`` (6) and
    ``exists`` (7) out, both the input passed through."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "in", path_type, "file path", 2)],
                outputs=[
                    pane(vi, "output", "dir", flag_type, "is directory", 6),
                    pane(vi, "output", "ok", flag_type, "exists", 7),
                ],
                data_flow=[wire(vi, "in", "dir"), wire(vi, "in", "ok")],
            ),
            [],
        )

    return build


def _empty(vi: str) -> tuple[VIContext, list[GraphNode]]:
    return VIContext(name=vi), []


def _plain_call(wrap: str, path_type: LVType) -> Recipe:
    """A VI whose plain subVI call of ``wrap`` records no variant, only the
    types of its terminals: an unused slot (0), ``file path`` (2) of
    ``path_type``, and two Boolean outputs (6, 7)."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        node = call(
            vi,
            "w",
            wrap,
            "iUse",
            [("unused", 0, VOID), ("file path", 2, path_type)],
            [("is directory", 6, BOOL), ("exists", 7, BOOL)],
        )
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "p", path_type, "p", 0)],
                outputs=[pane(vi, "output", "r", BOOL, "r", 1)],
                data_flow=[wire(vi, "p", "w.file path"), wire(vi, "w.exists", "r")],
            ),
            [node],
        )

    return build


def _file_exists_project(tmp_path: Path, variants: dict[str, Recipe]) -> Project:
    """The polymorphic VI ``Wrap.vi`` with ``variants`` and ``Caller.vi``, whose
    plain call of it records a scalar Path and two Booleans."""
    project = Project(tmp_path)
    keys = [project.add_vi(name, recipe) for name, recipe in variants.items()]
    wrap = project.add_vi("Wrap", _empty)
    project.graph._poly_info[wrap] = PolyInfo(variant_keys=keys)  # noqa: SLF001
    project.add_vi("Caller", _plain_call(wrap, PATH))
    return project


def test_a_call_that_records_no_variant_runs_the_one_its_types_match(
    tmp_path: Path,
) -> None:
    """The File Exists shape: the call records a scalar Path and Booleans, which
    is the Scalar variant's pane and not the Array one's."""
    project = _file_exists_project(
        tmp_path,
        {
            "Scalar": _file_exists(PATH, BOOL),
            "Array": _file_exists(_array(PATH), _array(BOOL)),
        },
    )
    caller, scalar = project.vis["Caller"], project.vis["Scalar"]
    assert project.graph.poly_call_choices() == {
        project.vis["Wrap"]: {caller: {scalar}}
    }
    package = project.generate()
    assert package.gaps == []
    source = package.sources["caller"]
    assert "from .scalar import scalar\n" in source
    assert "scalar_result = scalar(file_path=p)" in source
    with package.use() as module:
        assert module("caller").caller(p="here").r == "here"


def _gap_reasons(project: Project) -> list[str]:
    return [gap.reason for gap in project.generate().gaps]


def test_types_that_match_two_variants_are_a_gap_naming_those_two(
    tmp_path: Path,
) -> None:
    project = _file_exists_project(
        tmp_path,
        {
            "First": _file_exists(PATH, BOOL),
            "Paths": _file_exists(_array(PATH), _array(BOOL)),
            "Second": _file_exists(PATH, BOOL),
        },
    )
    assert _gap_reasons(project) == [
        "The call of the polymorphic VI Wrap.vi records no variant, and the "
        "connector panes of several of its variants match this call: First.vi, "
        "Second.vi"
    ]


_NO_MATCH = (
    "The call of the polymorphic VI Wrap.vi records no variant, and no variant's "
    "connector pane matches this call; its variants: "
)


def test_types_that_match_no_variant_are_a_gap(tmp_path: Path) -> None:
    project = _file_exists_project(
        tmp_path,
        {
            "Paths": _file_exists(_array(PATH), _array(BOOL)),
            "Numbers": _file_exists(INT, BOOL),
        },
    )
    assert _gap_reasons(project) == [_NO_MATCH + "Paths.vi, Numbers.vi"]


def test_a_variant_that_is_not_loaded_matches_nothing(tmp_path: Path) -> None:
    """The variant whose pane would match is not loaded: its pane is unknown, so
    the call is a gap."""
    project = Project(tmp_path)
    paths = project.add_vi("Paths", _file_exists(_array(PATH), _array(BOOL)))
    absent = str((tmp_path / "src" / "Scalar.vi").resolve())
    wrap = project.add_vi("Wrap", _empty)
    project.graph._poly_info[wrap] = PolyInfo(variant_keys=[absent, paths])  # noqa: SLF001
    project.add_vi("Caller", _plain_call(wrap, PATH))
    assert _gap_reasons(project) == [_NO_MATCH + "Scalar.vi, Paths.vi"]


def test_a_class_owned_polymorphic_vi_is_the_same_gap(tmp_path: Path) -> None:
    """A polymorphic VI a class lists is no method: a call that runs none of its
    variants is the polymorphic VI's gap, not a method's."""
    project = Project(tmp_path)
    project.add_class("Cls", [])
    paths = project.add_vi("Paths", _file_exists(_array(PATH), _array(BOOL)))
    wrap = project.add_method("Cls", "Wrap", _empty, static=True)
    project.graph._poly_info[wrap] = PolyInfo(variant_keys=[paths])  # noqa: SLF001
    project.add_vi("Caller", _plain_call(wrap, PATH))
    assert _gap_reasons(project) == [_NO_MATCH + "Paths.vi"]


def test_a_static_reference_to_a_polymorphic_vi_is_a_gap(tmp_path: Path) -> None:
    """A VI reference names the polymorphic VI, which has no function of its
    own."""
    project = Project(tmp_path)
    scalar = project.add_vi("Scalar", _file_exists(PATH, BOOL))
    wrap = project.add_vi("Wrap", _empty)
    project.graph._poly_info[wrap] = PolyInfo(variant_keys=[scalar])  # noqa: SLF001
    project.add_vi("Runner", runner(wrap, "True"))
    assert set(_gap_reasons(project)) == {
        "This use of the polymorphic VI Wrap.vi records no variant, so which one "
        "it runs is not known; its variants: Scalar.vi"
    }


_EASYXML = Path(".lvkit/cache/samples/JKI-EasyXML/Source/Easy Write XML File.vi")


@pytest.mark.needs_samples
def test_easy_write_xml_file_runs_the_scalar_file_exists(tmp_path: Path) -> None:
    """The plain call of the polymorphic ``File Exists__ogtk.vi`` records a scalar
    path: it runs the Scalar variant, a catalog template, written through the
    copy of the call that carries the variant."""
    result = generate_python(_EASYXML, tmp_path / "gen", userlib_root=OPENG_USERLIB)
    graph = result["graph"]
    calls = [
        node
        for node in graph.iter_nodes(str(_EASYXML.resolve()))
        if isinstance(node, VINode) and (node.name or "") == "File Exists__ogtk.vi"
    ]
    assert len(calls) == 1 and calls[0].variant_key is None
    assert Path(graph.call_variant(calls[0])).name == "File Exists - Scalar__ogtk.vi"
    source = next((tmp_path / "gen").rglob("easy_write_xml_file.py")).read_text()
    assert "Path(xml_file_path).exists()" in source
    assert not [g for g in result["gaps"] if "File Exists" in g.reason]
