"""A diagram that cannot be drawn is a listed problem of that VI: the page is
still written, the problem is named by the VI's project-relative path, and every
problem is listed."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lvkit.docs import generate as docs_generate
from lvkit.docs.generate import generate_documents
from lvkit.run_report import ProblemStage, RunProblems

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
    run = RunProblems()
    summary = generate_documents(
        str(_project(tmp_path)), str(tmp_path / "out"), run=run
    )
    problems = run.report().problems
    assert problems
    first = problems[0]
    assert (first.vi, first.stage) == ("Main.vi", ProblemStage.DOCUMENT)
    assert "no room" in first.reason
    assert str(tmp_path) not in "\n".join(p.line for p in problems)
    assert "files skipped" not in summary
    assert list((tmp_path / "out").rglob("*.html"))
