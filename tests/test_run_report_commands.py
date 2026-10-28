"""The one problem rule in the commands that read many VIs or one VI: render,
docs, describe, diff, visualize, structure, generate and unresolved each end with
the same summary and exit code when a VI in the project cannot be read."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lvkit import cli
from lvkit.cache_paths import project_root
from lvkit.docs import generate as docs_generate
from lvkit.docs.page_names import PageName, page_names
from lvkit.graph import InMemoryVIGraph, LoadMode

GOOD_VI = Path(__file__).parent / "corpus" / "issues" / "132" / "Run.vi"


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    shutil.copy(GOOD_VI, root / "sub" / "Good.vi")
    (root / "Bad.vi").write_text("not a vi", encoding="utf-8")
    return root


def _lvkit(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["lvkit", *argv])
    return cli.main()


def _summary(err: str) -> list[str]:
    """The summary lines (after the warnings printed as the run went)."""
    lines = err.splitlines()
    head = next(
        i for i, ln in enumerate(lines) if ln.startswith(("Partial", "Stopped"))
    )
    return lines[head:]


def test_render_directory_reports_the_unreadable_vi_and_exits_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path)
    code = _lvkit(monkeypatch, "render", str(root))
    captured = capsys.readouterr()
    assert code == 3
    assert "2 files — 1 rendered, 0 already fresh, 1 failed" in captured.out
    assert "warning: Bad.vi: render: pylabview could not read the file" in captured.err
    assert _summary(captured.err)[0] == "Partial: 1 problem(s)"
    assert str(tmp_path) not in captured.err


def test_render_directory_fail_fast_stops_at_the_first_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _lvkit(monkeypatch, "render", str(_project(tmp_path)), "--fail-fast")
    assert code == 1
    assert _summary(capsys.readouterr().err)[0] == "Stopped: 1 problem(s)"


def test_render_of_one_unreadable_vi_stops_with_its_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path)
    code = _lvkit(monkeypatch, "render", str(root / "Bad.vi"))
    err = capsys.readouterr().err
    assert code == 1
    assert "warning: Bad.vi: render: pylabview could not read the file" in err
    assert "Stopped: 1 problem(s)" in err
    assert "Traceback" not in err


def test_describe_of_a_good_vi_is_complete_and_of_a_bad_one_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path)
    assert _lvkit(monkeypatch, "describe", str(root / "sub" / "Good.vi")) == 0
    assert "Stopped" not in capsys.readouterr().err
    assert _lvkit(monkeypatch, "describe", str(root / "Bad.vi")) == 1
    err = capsys.readouterr().err
    assert "warning: Bad.vi: describe: pylabview could not read the file" in err
    assert "Stopped: 1 problem(s)" in err


def test_diff_names_the_unreadable_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path)
    code = _lvkit(
        monkeypatch, "diff", str(root / "sub" / "Good.vi"), str(root / "Bad.vi")
    )
    err = capsys.readouterr().err
    assert code == 1
    assert "warning: Bad.vi: diff:" in err and "Stopped: 1 problem(s)" in err


def test_visualize_of_one_unreadable_vi_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path)
    out = tmp_path / "g.html"
    code = _lvkit(monkeypatch, "visualize", str(root / "Bad.vi"), "-o", str(out))
    err = capsys.readouterr().err
    assert code == 1
    assert "warning: Bad.vi: visualize:" in err


def test_structure_of_an_unreadable_project_file_stops_with_its_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "Broken.lvclass"
    broken.write_text("<not xml", encoding="utf-8")
    code = _lvkit(monkeypatch, "structure", str(broken))
    err = capsys.readouterr().err
    assert code == 1
    assert "warning: Broken.lvclass: structure:" in err
    assert "Stopped: 1 problem(s)" in err


def test_generate_directory_reports_the_unreadable_vi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path)
    code = _lvkit(monkeypatch, "generate", str(root), "-o", str(tmp_path / "out"))
    err = capsys.readouterr().err
    assert code == 3
    assert "warning: Bad.vi: extract: pylabview could not read the file" in err
    assert _summary(err)[0].startswith("Partial:")


def test_unresolved_reports_the_unreadable_vi_in_its_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path)
    code = _lvkit(monkeypatch, "unresolved", str(root), "--json")
    payload = json.loads(capsys.readouterr().out)
    assert code == 3
    assert payload["status"] == "Partial"
    assert [p["vi"] for p in payload["problems"]] == ["Bad.vi"]
    assert isinstance(payload["items"], list)


def test_docs_reports_every_skipped_file_and_names_pages_by_project_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path)
    for n in range(12):  # more skipped files than any cut-off
        (root / f"Bad{n}.vi").write_text("not a vi", encoding="utf-8")
    out = tmp_path / "docs"
    code = _lvkit(monkeypatch, "docs", str(root), str(out))
    captured = capsys.readouterr()
    assert code == 3
    summary = _summary(captured.err)
    assert summary[0] == "Partial: 13 problem(s)"
    listed = [line.strip() for line in summary[1:]]
    assert len(listed) == 13
    for n in range(12):
        assert any(line.startswith(f"warning: Bad{n}.vi: extract:") for line in listed)
    assert any(line.startswith("warning: Bad.vi: extract:") for line in listed)
    assert "and 1 more" not in captured.out + captured.err
    pages = sorted(p.relative_to(out).as_posix() for p in out.rglob("*.html"))
    assert "Project/sub_Good.html" in pages
    assert not [p for p in pages if str(tmp_path.name) in p or "tmp" in p]
    assert str(tmp_path) not in "".join(
        f.read_text(encoding="utf-8") for f in out.rglob("*.html")
    )


# ---- page names ---------------------------------------------------------------


def test_page_names_use_project_relative_and_recorded_library_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    vilib = tmp_path / "vi.lib" / "Utility"
    vilib.mkdir(parents=True)
    for target in (root / "sub" / "a b.vi", root / "sub" / "a_b.vi", vilib / "Y.vi"):
        shutil.copy(GOOD_VI, target)
    graph = InMemoryVIGraph()
    graph.set_library_roots(vilib_root=tmp_path / "vi.lib")
    for target in (root / "sub" / "a b.vi", root / "sub" / "a_b.vi", vilib / "Y.vi"):
        graph.load_vi(target, LoadMode.NONE)
    names = page_names(graph, graph.list_vis(), root)
    assert sorted((n.group, n.stem) for n in names.values()) == [
        ("Project", "sub_a_b"),
        ("Project", "sub_a_b-2"),
        ("vi.lib", "Utility_Y"),
    ]


def test_a_long_page_name_is_cut_with_a_hash(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    deep = root.joinpath(*["very_long_folder_name_here"] * 8)
    deep.mkdir(parents=True)
    shutil.copy(GOOD_VI, deep / "X.vi")
    graph = InMemoryVIGraph()
    graph.load_vi(deep / "X.vi", LoadMode.NONE)
    (name,) = page_names(graph, graph.list_vis(), root).values()
    assert isinstance(name, PageName) and len(name.stem) <= 100


def test_a_terminal_without_a_label_does_not_break_the_poly_variant_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue 212 B: a polymorphic VI's variants listed terminals by name, and a
    terminal with none made sorting the names raise."""
    unnamed = MagicMock(index=3, lv_type=None)
    unnamed.name = None
    named = MagicMock(index=1, lv_type=None)
    named.name = "x"
    graph = MagicMock()
    for method in (
        "get_outputs",
        "top_level_nodes",
        "get_constants",
        "get_wires",
        "get_off_pane_inputs",
        "get_off_pane_outputs",
        "get_vi_dependencies",
        "get_vi_dependents",
    ):
        getattr(graph, method).return_value = []
    graph.get_inputs.return_value = [unnamed, named]
    graph.is_typedef.return_value = False
    graph.get_owning_class.return_value = None
    monkeypatch.setattr(
        docs_generate, "render_vi_with_subvis", lambda g, n: (None, {})
    )
    data = docs_generate._prepare_vi_documentation_data(
        "P.vi", graph, {"P.vi": ["V1.vi"]}
    )
    (variant,) = data["variant_params"]
    assert sorted(i["name"] for i in variant["inputs"]) == ["input_3", "x"]


_PICO_LLB = Path(
    ".lvkit/cache/samples/picosdk-ni-labview-examples/ps3000/32-bit-examples/"
    "PicoScope3000.llb"
)


@pytest.mark.needs_samples
@pytest.mark.skipif(not _PICO_LLB.is_file(), reason="PicoScope sample absent")
def test_a_vi_inside_an_llb_is_named_by_the_llb_not_the_cache_folder() -> None:
    graph = InMemoryVIGraph()
    graph.load_llb(_PICO_LLB, LoadMode.MINIMAL)
    names = page_names(graph, graph.list_vis(), Path("/no/such/project"))
    assert names
    for vi_name, name in names.items():
        assert name.group == project_root(_PICO_LLB).name  # not the cache
        assert "PicoScope3000.llb" in name.stem
        assert "llb-e" not in name.stem  # the cache folder's hashed name
        label = graph.vi_label(vi_name)
        assert label.startswith("ps3000/32-bit-examples/PicoScope3000.llb/")
        assert label.endswith(Path(vi_name).name)


_TWO_LIBS = Path(__file__).parent / "corpus" / "issues" / "29" / "Test LVKit"


def _project_with_an_unreadable_subvi(tmp_path: Path) -> Path:
    root = tmp_path / "Test LVKit"
    shutil.copytree(_TWO_LIBS, root)
    (root / "Lib2" / "Class" / "Do.vi").write_text("not a vi", encoding="utf-8")
    return root / "Lib2" / "Class" / "Test.vi"


@pytest.mark.parametrize("command", ["render", "diff"])
def test_an_unreadable_subvi_is_reported_on_a_miss_and_on_a_hit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    vi = _project_with_an_unreadable_subvi(tmp_path)
    argv = [command, str(vi)] + ([str(vi)] if command == "diff" else [])
    fmt = "svg" if command == "render" else "text"
    reports = []
    for attempt in ("miss", "hit"):
        if attempt == "hit":  # the second run must be served from the cache

            def no_build(*args: object, **kwargs: object) -> None:
                raise AssertionError("rebuilt on a cache hit")

            monkeypatch.setattr("lvkit.render.body.render_body_with_name", no_build)
            monkeypatch.setattr("lvkit.vi_diff.diff_vi_files", no_build)
        code = _lvkit(monkeypatch, *argv, "--format", fmt)
        err = capsys.readouterr().err
        assert code == 3
        assert "Do.vi: extract: pylabview could not read the file" in err
        reports.append(_summary(err))
    assert reports[0] == reports[1]
