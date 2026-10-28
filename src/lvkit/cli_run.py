"""What every multi-VI command shares on the command line: ``--fail-fast``, the
run's problems, and its closing summary and exit code (see ``run_report``)."""

from __future__ import annotations

import argparse
import sys
import traceback
from collections.abc import Callable
from pathlib import Path

from .cache_paths import project_root
from .run_report import (
    EXIT_STOPPED,
    Outcome,
    ProblemKind,
    ProblemStage,
    RunProblems,
    RunSettings,
    RunStopped,
)


def add_fail_fast_arg(parser: argparse.ArgumentParser) -> None:
    """Add ``--fail-fast`` to a command that processes many VIs."""
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help=(
            "End the run at the first problem: its warning is printed, nothing "
            "more is processed, and the exit code is 1 (the result is incomplete)."
        ),
    )


def command_run(
    args: argparse.Namespace,
    input_path: Path | None = None,
    root: Path | None = None,
) -> RunProblems:
    """The problems of one command run: each is printed to stderr as it is found.
    Paths are shown relative to ``root``, else to the project ``input_path`` is in
    (never to the folder the user happened to pass)."""
    if root is None and input_path is not None:
        root = project_root(input_path)
    run = RunProblems(
        RunSettings(
            echo=True, fail_fast=getattr(args, "fail_fast", False), root=root
        )
    )
    args.run = run  # for `run_command`, which reports it if a fail-fast stop ends it
    return run


def finish(run: RunProblems, stopped: bool = False) -> int:
    """Print the summary on stderr when the run was not Complete and return the
    run's exit code."""
    report = run.report()
    if report.outcome(stopped) is not Outcome.COMPLETE:
        report.print_summary(stopped, sys.stderr)
    return report.exit_code(stopped)


def stop_on(
    run: RunProblems,
    path: Path | str,
    stage: ProblemStage,
    exc: Exception,
    kind: ProblemKind | None = None,
) -> int:
    """End a run because the one VI (or file) it is about failed with ``exc``:
    record the problem, print the traceback when it is an lvkit error (the same
    in every command), print the summary and return the exit code."""
    problem = run.fail(path, stage, exc, kind)
    if problem.kind is ProblemKind.LVKIT_ERROR:
        traceback.print_exception(exc)
    return finish(run, stopped=True)


def run_command(
    command: Callable[[argparse.Namespace], int], args: argparse.Namespace
) -> int:
    """Run one command; a fail-fast run that reached its first problem ends here
    with the summary of everything recorded so far and exit code 1, whichever
    command it was in."""
    args.run = None
    try:
        return command(args)
    except RunStopped as stop:
        run = args.run or RunProblems()
        run.take([stop.problem], announce=False)
        report = run.report()
        if run.on_stop is not None:
            run.on_stop(report)
        report.print_summary(stopped=True)
        return EXIT_STOPPED
