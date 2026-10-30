"""A diagram that cannot be drawn is a problem of that VI: the page is still
written, the problem is named by the VI's project-relative path, every problem is
listed, and the docs run is partial (exit 3)."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pytest

from lvkit import cli
from lvkit.docs import generate as docs_generate
from lvkit.docs.generate import generate_documents

MAIN = Path(__file__).parent / "corpus" / "issues" / "39" / "zorder-not-respected.vi"


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / ".lvkit").mkdir(parents=True)
    return Path(shutil.copy(MAIN, project / "Main.vi"))


def _fail_to_draw(*_args: object, **_kwargs: object) -> None:
    raise ValueError("no room")


def test_an_undrawable_diagram_is_a_listed_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(docs_generate, "render_vi_with_subvis", _fail_to_draw)
    result = generate_documents(str(_project(tmp_path)), str(tmp_path / "out"))
    assert not result.stopped
    assert result.problems
    assert all(
        p.startswith("Main.vi: the diagram could not be drawn")
        for p in result.problems[:1]
    )
    assert str(tmp_path) not in "\n".join(result.problems)
    assert "files skipped" not in result.summary
    assert list((tmp_path / "out").rglob("*.html"))


def test_docs_exits_partial_when_there_are_problems(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(docs_generate, "render_vi_with_subvis", _fail_to_draw)
    args = argparse.Namespace(
        input_path=str(_project(tmp_path)),
        output_dir=str(tmp_path / "out"),
        search_paths=None,
        vilib_root=None,
        userlib_root=None,
    )
    monkeypatch.setattr(cli, "_configure_resolvers", lambda _args: None)
    monkeypatch.setattr(cli, "_parse_library_roots", lambda _args: (None, None))
    monkeypatch.setattr(cli, "_auto_search_paths", lambda *_a: [])
    monkeypatch.setattr(
        cli, "_resolve_load_mode", lambda *_a: docs_generate.LoadMode.NONE
    )
    assert cli.cmd_docs(args) == 3
