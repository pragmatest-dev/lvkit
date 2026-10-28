"""What every MCP tool adds to its result: the problems found with the VIs it used.

A tool never fails because one VI could not be read: it returns what it could,
with ``status`` (Complete / Partial / Stopped) and the full ``problems`` list, the
same list the command line prints. A call fails only for a bad argument. When the
one VI a tool is about cannot be read there is no result to give, so the answer is
``status`` ``Stopped`` and the problem that says why.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...cache_paths import project_root
from ...run_report import ProblemStage, RunProblems, RunSettings


def new_run(path: Path | str | None = None) -> RunProblems:
    """The problems of one tool call: nothing is printed (the client gets the
    list in the result) and nothing is shared with another call. Paths are shown
    relative to the project ``path`` is in."""
    root = project_root(path) if path is not None else None
    return RunProblems(RunSettings(root=root))


def with_problems(
    result: dict[str, Any], run: RunProblems, stopped: bool = False
) -> dict[str, Any]:
    """``result`` plus ``status`` and ``problems`` projected from the run."""
    return {**result, **run.report().to_json(stopped)}


def stopped_on(
    run: RunProblems, path: Path | str, stage: ProblemStage, exc: Exception
) -> dict[str, Any]:
    """The answer when the VI a tool is about cannot be read: no result, the
    problem recorded the way the command line records it, status ``Stopped``."""
    run.fail(path, stage, exc)
    return with_problems({}, run, stopped=True)
