"""``generate`` and ``unresolved`` over a project whose VI has a gap: the warning
lines, the exit code, the records, and the stops that are not gaps."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from lvkit import cli
from lvkit.codegen.builder import build_module
from lvkit.codegen.gaps import Gap
from lvkit.codegen.global_module import build_global_module
from lvkit.codegen.type_module import build_type_module
from lvkit.graph.models import Constant, VIContext, VINode, Wire
from lvkit.models import (
    FPTerminal,
    LVType,
    LVTypeKind,
    UndecodedPicture,
    ValuesNotKept,
)
from lvkit.module_layout import ModuleCollisionError
from tests.helpers import build_graph
from tests.test_ext_func_library import Project


@pytest.fixture
def project(tmp_path: Path) -> Iterator[Project]:
    yield Project(tmp_path)
    for name in [m for m in sys.modules if m.split(".")[0] == "clfn_bdhb"]:
        del sys.modules[name]


def _missing_library_vi(project: Project) -> Path:
    return project.write_vi("vendorlib.*", ["", "..", "libs", "vendorlib.dll"], "swap")


def _lvkit(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["lvkit", *argv])
    return cli.main()


def test_generate_warns_once_per_gap_and_exits_two(
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vi = _missing_library_vi(project)
    code = _lvkit(monkeypatch, "generate", str(vi), "-o", str(project.base / "out"))
    captured = capsys.readouterr()
    assert code == 2
    warnings = [
        line for line in captured.err.splitlines() if line.startswith("warning: ")
    ]
    assert len(warnings) == 1
    assert warnings[0].startswith(
        "warning: vis/sub/Clfn_BDHb.xml: lib.dll:swap: The Call Library Function "
        "Node 'swap' records the library 'vendorlib.*'"
    )
    assert "no file vendorlib.dll or vendorlib.so or vendorlib.framework" in warnings[0]
    assert "  gaps:  1\n" in captured.out
    assert (
        "Partial: 1 VIs generated, 1 with gaps (1 gaps) — see warnings\n"
        in captured.out
    )
    # The warning carries the kind and the diagnostic, not only the reason.
    assert warnings[0].endswith("[unsupported_node]")


def test_generate_result_carries_the_gap_records(project: Project) -> None:
    vi = _missing_library_vi(project)
    project.generate(vi)
    assert project.result is not None
    (gap,) = project.result["gaps"]
    assert isinstance(gap, Gap)
    assert gap.vi.endswith("Clfn_BDHb.xml")
    assert gap.kind == "unsupported_node"
    assert (project.result["error"], project.result["failed"]) == (0, 0)


def test_unresolved_lists_the_gaps_generate_warns_about(
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vi = _missing_library_vi(project)
    project.generate(vi)
    assert project.result is not None
    gaps = project.result["gaps"]
    capsys.readouterr()
    assert _lvkit(monkeypatch, "unresolved", str(vi), "--json") == 0
    items = json.loads(capsys.readouterr().out)
    assert [set(item) for item in items] == [
        {"kind", "identifier", "name", "count", "vi_names"}
    ]
    assert [(i["kind"], i["identifier"], i["name"]) for i in items] == [
        (g.kind, g.identifier, g.name) for g in gaps
    ]
    assert items[0]["vi_names"] == ["vis/sub/Clfn_BDHb.xml"]


def test_a_module_collision_prints_its_message_and_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vi = tmp_path / "Any.vi"
    vi.write_bytes(b"")

    def collide(*args: object, **kwargs: object) -> dict:
        raise ModuleCollisionError("'/a/X Y.vi' and '/a/X_Y.vi' both generate x_y.")

    monkeypatch.setattr("lvkit.pipeline.generate_python", collide)
    code = _lvkit(monkeypatch, "generate", str(vi), "-o", str(tmp_path / "out"))
    err = capsys.readouterr().err
    assert code == 1
    assert "Error: '/a/X Y.vi' and '/a/X_Y.vi' both generate x_y." in err
    assert "Traceback" not in err


@pytest.mark.parametrize(
    "result,code",
    [
        ({"error": 0, "failed": 0, "gaps": []}, 0),
        ({"error": 0, "failed": 0, "gaps": [object()]}, 2),
        ({"error": 1, "failed": 0, "gaps": []}, 1),
        ({"error": 0, "failed": 1, "gaps": []}, 1),
        ({"error": 1, "failed": 0, "gaps": [object()]}, 1),
    ],
)
def test_exit_code_is_0_for_complete_2_for_partial_1_for_a_stopped_vi(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: dict,
    code: int,
) -> None:
    vi = tmp_path / "Any.vi"
    vi.write_bytes(b"")
    monkeypatch.setattr("lvkit.pipeline.generate_python", lambda *a, **k: result)
    assert _lvkit(monkeypatch, "generate", str(vi), "-o", str(tmp_path)) == code


def test_generate_help_documents_the_exit_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        _lvkit(monkeypatch, "generate", "--help")
    help_text = capsys.readouterr().out
    assert "0  every VI converted, no gaps" in help_text
    assert "2  partial" in help_text
    assert "1  a VI could not be written" in help_text


def test_a_run_without_gaps_says_complete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from lvkit.pipeline import generate_python
    # A directory with no VIs is a run with nothing to convert.
    (tmp_path / "empty").mkdir()
    result = generate_python(tmp_path / "empty", tmp_path / "out")
    assert result["gaps"] == []
    assert "Complete: 0 VIs generated, no gaps" in capsys.readouterr().out


def _two_vis_with_a_gap_each(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """A folder of two VIs that each call the missing library (the loader is told
    to take the block-diagram files for VIs)."""
    first = _missing_library_vi(project)
    folder = first.parent
    for suffix in ("_BDHb.xml", ".xml"):
        (folder / f"Second{suffix}").write_text(
            (folder / f"Clfn{suffix}").read_text()
        )
    monkeypatch.setattr(
        "lvkit.graph.loading.rglob_vi_files",
        lambda _: [folder / "Clfn_BDHb.xml", folder / "Second_BDHb.xml"],
    )
    return folder


def _modules(out: Path) -> list[str]:
    return sorted(p.name for p in out.rglob("*.py") if p.name != "__init__.py")


def test_fail_fast_ends_the_run_at_the_first_gap(
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    folder = _two_vis_with_a_gap_each(project, monkeypatch)
    out = project.base / "out"
    code = _lvkit(monkeypatch, "generate", str(folder), "-o", str(out), "--fail-fast")
    captured = capsys.readouterr()
    assert code == 1
    warnings = [
        line for line in captured.err.splitlines() if line.startswith("warning: ")
    ]
    assert len(warnings) == 1
    assert "Stopped at the first gap (--fail-fast)" in captured.out
    assert "Complete" not in captured.out and "Partial" not in captured.out
    assert _modules(out) == []


def test_without_fail_fast_the_same_run_writes_both_vis_and_exits_two(
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    folder = _two_vis_with_a_gap_each(project, monkeypatch)
    out = project.base / "out"
    code = _lvkit(monkeypatch, "generate", str(folder), "-o", str(out))
    captured = capsys.readouterr()
    assert code == 2
    warnings = [
        line for line in captured.err.splitlines() if line.startswith("warning: ")
    ]
    assert len(warnings) == 2
    assert _modules(out) == ["clfn_bdhbxml.py", "second_bdhbxml.py"]
    assert "Partial: 2 VIs generated, 2 with gaps (2 gaps)" in captured.out


# ---------------------------------------------------------------------------
# Gaps in no node
# ---------------------------------------------------------------------------


def test_a_constant_with_no_value_to_write_raises_first_in_the_function() -> None:
    count = 100_001
    array = LVType(
        kind=LVTypeKind.ARRAY,
        dimensions=1,
        element_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumUInt8"),
    )
    context = VIContext(
        name="Const.vi",
        outputs=[
            FPTerminal(
                id="fp:y",
                index=0,
                direction="output",
                name="y",
                is_public=True,
                is_indicator=True,
                lv_type=array,
            )
        ],
        constants=[
            Constant(
                id="c",
                value="<100001 elements not kept>",
                lv_type=array,
                unwritable=ValuesNotKept(count, (count,)),
                label="Frame",
            )
        ],
        data_flow=[Wire.from_terminals(from_terminal_id="c", to_terminal_id="fp:y")],
    )
    graph = build_graph(context, "Const.vi", [])
    gaps: list[Gap] = []
    source = build_module(context, "Const.vi", graph=graph, gaps=gaps)
    body = source.split("def const() -> ConstResult:\n")[1].splitlines()
    assert body[0].startswith("    raise NotImplementedError(")
    assert "'Frame'" in body[0] and "100001 elements" in body[0]
    assert body[1] == "    return ConstResult(y=None)"
    (gap,) = gaps
    assert (gap.kind, gap.node_name, gap.node_id) == (
        "unsupported_vi",
        "Frame",
        None,
    )
    namespace: dict = {}
    exec(compile(source, "const", "exec"), namespace)  # noqa: S102
    with pytest.raises(NotImplementedError, match="Frame"):
        namespace["const"]()


class _GraphWithoutItsParent:
    def is_class(self, key: str) -> bool:
        return True

    def class_parent_key(self, key: str) -> None:
        return None

    def class_parent_name(self, key: str) -> str:
        return "Base.lvclass"


def test_a_class_whose_parent_is_not_loaded_raises_when_its_module_runs() -> None:
    gaps: list[Gap] = []
    source = build_type_module(
        _GraphWithoutItsParent(),  # type: ignore[arg-type]
        "/p/Child.lvclass",
        None,  # type: ignore[arg-type]
        gaps=gaps,
    )
    assert source == (
        "from __future__ import annotations\n"
        "raise NotImplementedError("
        "'Parent class Base.lvclass of /p/Child.lvclass is not loaded\\n"
        "gap kind: unsupported_vi\\nVI: Child.lvclass')"
    )
    (gap,) = gaps
    assert (gap.kind, gap.vi, gap.node_id) == (
        "unsupported_vi",
        "/p/Child.lvclass",
        None,
    )
    with pytest.raises(NotImplementedError, match="Base.lvclass"):
        exec(compile(source, "child", "exec"), {})  # noqa: S102


def test_a_global_control_with_no_value_to_write_raises_where_it_is_defined() -> None:
    picture = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Picture")
    node = VINode(
        id="/p/Globals.vi",
        vi_path="/p/Globals.vi",
        name="Globals.vi",
        terminals=[
            FPTerminal(
                id="fp:text",
                index=0,
                direction="input",
                name="Text",
                is_public=False,
                is_indicator=False,
                lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String"),
                default_value="hello",
            ),
            FPTerminal(
                id="fp:logo",
                index=1,
                direction="input",
                name="Logo",
                is_public=False,
                is_indicator=False,
                lv_type=picture,
                default_value="<Picture, 3 bytes of drawing not decoded>",
                unwritable=UndecodedPicture(3),
            ),
        ],
    )
    gaps: list[Gap] = []
    source = build_global_module(node, gaps=gaps)
    lines = source.splitlines()
    assert any(line.startswith("text: str") for line in lines)
    assert lines[-1].startswith("raise NotImplementedError(")
    assert "'Logo'" in lines[-1] and "Picture of 3 bytes" in lines[-1]
    (gap,) = gaps
    assert (gap.kind, gap.node_name) == ("unsupported_vi", "Logo")


# ---------------------------------------------------------------------------
# A call of a VI that was not found
# ---------------------------------------------------------------------------


def _caller_of(callee_key: str, found: bool, tmp_path: Path):
    """A VI ``caller.vi`` that calls ``callee_key`` (a stub in the graph unless
    ``found``) with one wired input and one wired output, and the import resolver
    of its package."""
    from lvkit.graph import InMemoryVIGraph
    from lvkit.pipeline import create_import_resolver

    graph = InMemoryVIGraph()
    graph._dep_graph.add_node(  # noqa: SLF001
        callee_key,
        node_type="vi",
        path_tokens=["<vilib>", "daqmx", "Flatten Channel String.vi"],
    )
    if not found:
        graph._stubs.add(callee_key)  # noqa: SLF001
    out = tmp_path / "pkg"
    out.mkdir()
    vi_paths = {callee_key: out / "flatten.py", "caller.vi": out / "caller.py"}
    resolver = create_import_resolver(
        "pkg", out, vi_paths, out / "caller.py", caller_key="caller.vi", graph=graph
    )
    call = VINode(
        id="call",
        vi_path="caller.vi",
        name="Flatten Channel String.vi",
        node_type="iUse",
        callee_key=callee_key,
        terminals=[
            FPTerminal(
                id="k:in",
                index=0,
                direction="input",
                lv_type=LVType(kind=LVTypeKind.ARRAY),
            ),
            FPTerminal(
                id="k:out",
                index=1,
                direction="output",
                name="result",
                lv_type=_STRING,
            ),
        ],
    )
    context = VIContext(
        name="caller.vi",
        inputs=[
            FPTerminal(
                id="fp:x",
                index=0,
                direction="input",
                name="x",
                is_public=True,
                is_indicator=False,
                lv_type=LVType(kind=LVTypeKind.ARRAY),
            )
        ],
        outputs=[
            FPTerminal(
                id="fp:y",
                index=0,
                direction="output",
                name="y",
                is_public=True,
                is_indicator=True,
                lv_type=_STRING,
            )
        ],
        data_flow=[
            Wire.from_terminals(from_terminal_id="fp:x", to_terminal_id="k:in"),
            Wire.from_terminals(from_terminal_id="k:out", to_terminal_id="fp:y"),
        ],
    )
    graph_ = build_graph(context, "caller.vi", [call], graph=graph)
    gaps: list[Gap] = []
    source = build_module(
        context, "caller.vi", import_resolver=resolver, graph=graph_, gaps=gaps
    )
    return source, gaps


_STRING = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")


def test_a_call_of_a_vi_that_was_not_found_is_one_missing_vi_gap(
    tmp_path: Path,
) -> None:
    key = "<vilib>/daqmx/Flatten Channel String.vi"
    source, gaps = _caller_of(key, False, tmp_path)
    (gap,) = gaps
    assert (gap.kind, gap.identifier, gap.node_name) == (
        "missing_vi",
        "Flatten Channel String.vi",
        "Flatten Channel String.vi",
    )
    assert gap.reason == (
        "The VI 'Flatten Channel String.vi' (<vilib>/daqmx/Flatten Channel String.vi)"
        " was not found; add it to the project or to a --search-path and generate "
        "again"
    )
    # The raise carries the types and directions of the terminals the call wires.
    assert "input index=0 type=array" in gap.message
    assert "output index=1 type=String" in gap.message
    # Nothing is imported, and the rest of the caller is there after the raise.
    assert "from .flatten" not in source
    assert source.index("raise NotImplementedError(") < source.index("return ")
    assert not list(tmp_path.rglob("flatten.py"))


def test_a_call_of_a_found_vi_with_an_unmapped_terminal_is_a_terminal_mapping_gap(
    tmp_path: Path,
) -> None:
    source, gaps = _caller_of("/proj/flatten.vi", True, tmp_path)
    assert [g.kind for g in gaps] == ["terminal_mapping"]
    assert "from .flatten import" not in source
