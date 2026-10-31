"""A VI copy (a git version written to a temporary folder) given the path of
the file it is a copy of reads its relative recorded paths from that file's
folder, and is that file: its identity and the project its paths are shown in.
Hermetic, through the fake extractor of ``tests.test_call_identity``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from lvkit import cli
from lvkit.extract_pool import ExtractionPool
from lvkit.graph import InMemoryVIGraph, load_vi_by_path
from lvkit.graph.describe import describe_vi
from lvkit.graph.loading import LoadMode
from lvkit.run_report import EXIT_PARTIAL, UnreadableVIError
from tests.test_call_identity import _Call, _call, _static, _Tree
from tests.test_class_member_calls import _write_class


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Tree:
    built = _Tree(tmp_path / "proj")
    monkeypatch.setattr("lvkit.graph.loading.extract_vi_xml", built.extract)
    monkeypatch.setattr("lvkit.structure.extract_vi_xml", lambda _p: (None, None, None))
    monkeypatch.setattr(ExtractionPool, "prefetch", lambda self, paths: None)
    built.vi("SubVIs/Helper.vi", "Helper.vi", [])
    built.vi(
        "Main.vi", "Main.vi", [], [_static(["Helper.vi"], ["", "SubVIs", "Helper.vi"])]
    )
    # A method in a class folder dispatching to its own class's other method.
    _write_class(built, "Cls", "Cls", {"Run.vi": "../Run.vi", "Go.vi": "../Go.vi"})
    built.vi("Cls/Run.vi", "Cls.lvclass:Run.vi", ["Cls.lvclass"])
    built.vi(
        "Cls/Go.vi",
        "Cls.lvclass:Go.vi",
        ["Cls.lvclass"],
        [
            _Call(
                node_class="dynIUse",
                label="Run.vi",
                dyn=(["Cls.lvclass", "Run.vi"], ["", "Cls.lvclass"]),
            )
        ],
    )
    return built


def _copy(tree: _Tree, rel: str, tmp_path: Path) -> Path:
    """``rel`` copied into an unrelated temporary folder, read through the same
    fake extraction."""
    original = (tree.root / rel).resolve()
    copy = tmp_path / "blob" / Path(rel).name
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_bytes(original.read_bytes())
    tree._xml[copy.resolve()] = tree._xml[original]  # noqa: SLF001
    return copy


def test_a_copy_without_its_original_misses_its_relative_subvi(
    tree: _Tree, tmp_path: Path
) -> None:
    copy = _copy(tree, "Main.vi", tmp_path)
    graph, key = load_vi_by_path(copy, LoadMode.MINIMAL)
    call = _call(graph, key)
    missing = str(copy.parent.resolve() / "SubVIs" / "Helper.vi")
    assert call.callee_key == missing
    assert graph.loaded_callee_key(call) is None
    assert graph.recorded_path(missing) == "/SubVIs/Helper.vi"


def test_a_copy_with_its_original_is_that_vi(tree: _Tree, tmp_path: Path) -> None:
    original = tree.root / "Main.vi"
    copy = _copy(tree, "Main.vi", tmp_path)
    graph, key = load_vi_by_path(copy, LoadMode.MINIMAL, original_path=original)
    helper = str((tree.root / "SubVIs" / "Helper.vi").resolve())
    assert key == str(original.resolve())
    assert graph.loaded_callee_key(_call(graph, key)) == helper
    assert graph.get_vi_source_path(key) == copy  # the bytes it was read from
    base, base_key = load_vi_by_path(original, LoadMode.MINIMAL)
    assert describe_vi(graph, key) == describe_vi(base, base_key)


def test_a_copy_of_a_class_method_resolves_through_its_class(
    tree: _Tree, tmp_path: Path
) -> None:
    original = tree.root / "Cls" / "Go.vi"
    copy = _copy(tree, "Cls/Go.vi", tmp_path)
    alone = InMemoryVIGraph()
    alone_key = alone.load_vi(copy, LoadMode.MINIMAL)
    assert alone_key is not None
    assert alone.loaded_callee_key(_call(alone, alone_key)) is None
    graph = InMemoryVIGraph()
    key = graph.load_vi(copy, LoadMode.MINIMAL, original_path=original)
    assert key is not None
    run = str((tree.root / "Cls" / "Run.vi").resolve())
    assert graph.loaded_callee_key(_call(graph, key)) == run


def test_original_path_applies_only_to_a_vi(tmp_path: Path) -> None:
    ctl = tmp_path / "x.ctl"
    ctl.write_bytes(b"")
    with pytest.raises(ValueError, match="applies to a VI file"):
        InMemoryVIGraph().load_vi(ctl, original_path=tmp_path / "y.ctl")


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "lvkit.cli", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("command", "flags"),
    [
        ("describe", ["--original-path"]),
        ("render", ["--original-path"]),
        ("diff", ["--before-original-path", "--after-original-path"]),
    ],
)
def test_the_commands_explain_the_option(command: str, flags: list[str]) -> None:
    help_text = " ".join(_cli(command, "--help").stdout.split())
    for flag in flags:
        assert f"{flag} PATH" in help_text
    assert "The file this VI is a copy of" in help_text


def test_an_original_in_a_missing_folder_is_a_usage_error(tmp_path: Path) -> None:
    vi = tmp_path / "a.vi"
    vi.write_bytes(b"")
    result = _cli("describe", str(vi), "--original-path", str(tmp_path / "no/a.vi"))
    assert result.returncode == 2
    assert "folder not found" in result.stderr


def test_a_copy_reports_its_problems_in_its_original_project(
    tree: _Tree, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The run of a copy shows paths in the original's project: a SubVI that cannot
    be read is a warning by its path there, and the run is Partial (exit 3)."""
    helper = (tree.root / "SubVIs" / "Helper.vi").resolve()
    real_extract = tree.extract

    def extract(path: Path) -> tuple[Path, Path | None, Path]:
        if Path(path).resolve() == helper:
            raise UnreadableVIError("no block diagram could be read", path=helper)
        return real_extract(path)

    monkeypatch.setattr("lvkit.graph.loading.extract_vi_xml", extract)
    original = tree.root / "Main.vi"
    copy = _copy(tree, "Main.vi", tmp_path)
    argv = ["lvkit", "describe", str(copy), "--original-path", str(original)]
    monkeypatch.setattr(sys, "argv", [*argv, "--no-auto-vilib"])
    assert cli.main() == EXIT_PARTIAL
    err = capsys.readouterr().err
    assert "warning: SubVIs/Helper.vi: extract: no block diagram could be read" in err
    assert str(tmp_path) not in err
