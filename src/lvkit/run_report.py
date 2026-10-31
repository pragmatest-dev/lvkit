"""The one way every lvkit command and MCP tool reports problems.

A problem with one VI never stops a run: it is recorded as a ``Problem`` on that
VI's node, the run continues, and the VI shows as unreadable wherever it is
referenced. Each problem prints as one warning line; a run ends with one summary
(Complete, Partial or Stopped, the full list of problems) and an exit code: 0
complete, 1 stopped or an lvkit error, 2 usage (the argument parser), 3 partial.

A problem is data on its VI's node in the graph (``graph/load_problems.py``); this
module has the problem type, how a run treats problems (``RunSettings``) and the
projection a command makes from the graph (``RunReport``). ``--fail-fast`` is
``RunSettings.fail_fast``: recording the first problem raises ``RunStopped``, which
is not an ``Exception``, so no per-VI handler that keeps a run going catches it.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from .cache_paths import project_root
from .llb_origin import llb_container_of

if TYPE_CHECKING:
    from .graph.core import InMemoryVIGraph

EXIT_COMPLETE = 0
EXIT_STOPPED = 1
EXIT_USAGE = 2
EXIT_PARTIAL = 3

ISSUES_URL = "https://github.com/pragmatest-dev/lvkit/issues"
LVKIT_ERROR_HINT = f"This is a bug in lvkit; please report it at {ISSUES_URL}"


class ProblemStage(str, Enum):
    """Where in a run the problem happened."""

    EXTRACT = "extract"
    LOAD = "load"
    INDEX = "index"
    GENERATE = "generate"
    RENDER = "render"
    DOCUMENT = "document"
    DESCRIBE = "describe"
    DIFF = "diff"
    VISUALIZE = "visualize"
    STRUCTURE = "structure"
    CACHE = "cache"


class ProblemKind(str, Enum):
    """What kind of problem it is."""

    # The VI file could not be read: it is shown as unreadable wherever it is
    # referenced, and nothing is derived from it.
    UNREADABLE = "unreadable"
    # lvkit itself failed on this VI: a bug to report.
    LVKIT_ERROR = "lvkit error"
    # The machine, not the VI or lvkit: lvkit could not write its cache (a full
    # disk, permissions, a Windows path over MAX_PATH).
    ENVIRONMENT = "environment"
    # Something in the VI cannot be converted to Python (see ``codegen/gaps.py``).
    GAP = "gap"
    # The VI's module could not be written (it has no file and no recorded path).
    UNWRITABLE = "unwritable"
    # A dependency was not at the path its caller records, and was loaded from
    # the VI search path instead (LabVIEW warns of the same). A note, not a
    # problem: the run is still complete (``Problem.is_note``).
    FOUND_ELSEWHERE = "found on the search path"
    # A front-panel control records no label anywhere the VI keeps one (its own
    # label part, the flat type's Label, the connector pane's names); it is
    # named ``control_<uid>``. A note: the VI is read whole.
    UNLABELED_CONTROL = "control without a label"


class Outcome(str, Enum):
    """How a run ended."""

    COMPLETE = "Complete"
    PARTIAL = "Partial"
    STOPPED = "Stopped"


# How the summary counts each kind of note.
_NOTE_PHRASES = {
    ProblemKind.FOUND_ELSEWHERE: (
        "file(s) loaded from the VI search path, not from the path their caller records"
    ),
    ProblemKind.UNLABELED_CONTROL: (
        "control(s) record no label and are named control_<uid>"
    ),
}


@dataclass(frozen=True)
class Problem:
    """One thing that went wrong with one VI (or one file) in a run."""

    # Project-relative path, or the recorded ``<vilib>/...`` path of a vi.lib VI.
    vi: str
    stage: ProblemStage
    kind: ProblemKind
    reason: str
    # The node the problem is in, when it is in one.
    node: str | None = None
    # Lines that say more than the reason (pylabview's block warnings).
    detail: tuple[str, ...] = ()
    hint: str | None = None
    # What the problem is, finer than ``kind`` (a gap's kind, ``unsupported_node``).
    code: str | None = None

    @property
    def is_note(self) -> bool:
        """Whether this is a note: the run went right, and the user may want to
        know (a file found on the VI search path, not at its recorded path; a
        control that records no label)."""
        return self.kind in (ProblemKind.FOUND_ELSEWHERE, ProblemKind.UNLABELED_CONTROL)

    @property
    def line(self) -> str:
        """The one warning line the problem prints."""
        where = f"{self.node}: " if self.node else ""
        tag = f" [{self.code}]" if self.code else ""
        return f"warning: {self.vi}: {self.stage.value}: {where}{self.reason}{tag}"

    def lines(self) -> list[str]:
        """The warning line, then its detail and hint, indented."""
        extra = [*self.detail, *([self.hint] if self.hint else [])]
        return [self.line, *(f"    {text}" for text in extra)]

    def to_json(self) -> dict[str, object]:
        return {
            "vi": self.vi,
            "stage": self.stage.value,
            "kind": self.kind.value,
            "reason": self.reason,
            "node": self.node,
            "detail": list(self.detail),
            "hint": self.hint,
            "code": self.code,
        }


def home_relative(path: Path) -> str:
    """``path`` as shown to a user: under their home folder it starts with ``~/``,
    so no user name appears."""
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


def problem_from_json(data: Mapping[str, Any]) -> Problem:
    """The ``Problem`` that ``Problem.to_json`` wrote."""
    return Problem(
        vi=data["vi"],
        stage=ProblemStage(data["stage"]),
        kind=ProblemKind(data["kind"]),
        reason=data["reason"],
        node=data.get("node"),
        detail=tuple(data.get("detail", ())),
        hint=data.get("hint"),
        code=data.get("code"),
    )


def problem_for_exception(vi: str, stage: ProblemStage, exc: Exception) -> Problem:
    """The problem a VI's failure is: an ``UnreadableVIError`` says the file could
    not be read; any other exception is lvkit's own failure on that VI."""
    if isinstance(exc, CacheWriteError):
        return Problem(
            vi=vi,
            stage=ProblemStage.CACHE,
            kind=ProblemKind.ENVIRONMENT,
            reason=exc.reason,
        )
    if isinstance(exc, UnreadableVIError):
        return Problem(
            vi=vi,
            stage=stage,
            kind=ProblemKind.UNREADABLE,
            reason=exc.reason,
            detail=exc.warnings,
        )
    return Problem(
        vi=vi,
        stage=stage,
        kind=ProblemKind.LVKIT_ERROR,
        reason=f"{type(exc).__name__}: {exc}",
        hint=LVKIT_ERROR_HINT,
    )


class UnreadableVIError(RuntimeError):
    """A VI file lvkit cannot read: pylabview could not extract it, or exported no
    block diagram. ``warnings`` are the lines pylabview printed while reading it."""

    def __init__(
        self,
        reason: str,
        warnings: tuple[str, ...] = (),
        path: Path | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.warnings = warnings
        # The file that could not be read, when the reader knows it.
        self.path = path


def declined(path: Path) -> UnreadableVIError:
    """Why a render or diff gave nothing: the diagram has no geometry to draw."""
    return UnreadableVIError(
        "nothing to draw: the diagram's geometry is missing", path=path
    )


class CacheWriteError(RuntimeError):
    """lvkit could not write its extraction cache: an environment problem (disk,
    permissions, path length), not a bug and not a bad VI."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RunStopped(BaseException):
    """A fail-fast run reached its first problem. Not an ``Exception``, so no
    handler that keeps a run going after a bad VI catches it."""

    def __init__(self, problem: Problem) -> None:
        self.problem = problem
        super().__init__(problem.reason)


@dataclass
class RunSettings:
    """How a run treats the problems it finds: ``echo`` prints each warning when it
    is recorded on its VI's node (the command line does; an MCP tool returns the
    list instead), ``fail_fast`` ends the run at the first one, and ``root`` is the
    project folder paths are shown relative to."""

    echo: bool = False
    fail_fast: bool = False
    root: Path | None = None
    stream: TextIO | None = None
    # False for ``unresolved``, whose result is the gaps: they are not problems.
    gaps_are_problems: bool = True
    # The LabVIEW library folders (``<vilib>``, ``<userlib>``, ``<instrlib>`` -> its
    # folders on disk, in order); a graph sets them with its library roots.
    library_roots: dict[str, tuple[Path, ...]] = field(default_factory=dict)
    _roots: dict[Path, Path] = field(default_factory=dict, repr=False)

    def label(self, path: Path | str) -> str:
        """How a user sees the file at ``path``: relative to its project, the
        ``<vilib>/...`` path below a library folder (likewise ``<userlib>``,
        ``<instrlib>``), or, for a VI inside an ``.llb``, the archive's label plus
        its name. Never an absolute path; a project folder itself is its name."""
        resolved = Path(path).resolve()
        container = llb_container_of(resolved)
        if container is not None:
            return f"{self.label(container)}/{resolved.name}"
        for token, libraries in self.library_roots.items():
            for library in libraries:
                if resolved.is_relative_to(library.resolve()):
                    rel = resolved.relative_to(library.resolve()).as_posix()
                    return f"{token}/{rel}"
        base = self.root.resolve() if self.root is not None else None
        if base is None or not resolved.is_relative_to(base):
            folder = resolved.parent
            if folder not in self._roots:
                self._roots[folder] = project_root(resolved)
            base = self._roots[folder]
        if resolved.is_relative_to(base):
            rel = resolved.relative_to(base).as_posix()
            return base.name if rel == "." else rel
        return resolved.name

    def announce(self, problem: Problem, may_stop: bool = True) -> None:
        """A problem was just recorded: print its warning when ``echo`` is on and
        stop a fail-fast run (unless the run is already ending, ``may_stop``
        False)."""
        if self.echo and not problem.is_note:
            out = self.stream or sys.stderr
            for text in problem.lines():
                print(text, file=out)
        if self.fail_fast and may_stop:
            raise RunStopped(problem)


def _order(problem: Problem) -> tuple[str, str, str, str]:
    return (problem.vi, problem.stage.value, problem.node or "", problem.reason)


@dataclass(frozen=True)
class RunReport:
    """The problems a command projects from the graph for the VIs it used, each
    once, in a fixed order, with the run's summary, JSON and exit code; its
    notes (``Problem.is_note``) apart, which leave the run complete."""

    problems: tuple[Problem, ...] = ()
    notes: tuple[Problem, ...] = ()

    @classmethod
    def of(cls, problems: Iterable[Problem]) -> RunReport:
        """Each failure once. An unreadable file is a fact about the file, so the
        same one found in two stages (unreadable when a caller loads it and again
        when it is rendered itself) is one problem. An lvkit error is a place in
        lvkit, so the same error from two stages stays two."""
        kept: dict[tuple[object, ...], Problem] = {}
        for problem in sorted(set(problems), key=_order):
            stage = problem.stage if problem.kind is ProblemKind.LVKIT_ERROR else None
            key = (
                problem.vi,
                problem.kind,
                problem.node,
                problem.reason,
                problem.code,
                stage,
            )
            kept.setdefault(key, problem)
        found = tuple(kept.values())
        return cls(
            tuple(p for p in found if not p.is_note),
            tuple(p for p in found if p.is_note),
        )

    def outcome(self, stopped: bool = False) -> Outcome:
        if stopped:
            return Outcome.STOPPED
        return Outcome.PARTIAL if self.problems else Outcome.COMPLETE

    def exit_code(self, stopped: bool = False) -> int:
        outcome = self.outcome(stopped)
        if outcome is Outcome.STOPPED or any(
            p.kind is ProblemKind.LVKIT_ERROR for p in self.problems
        ):
            return EXIT_STOPPED
        return EXIT_PARTIAL if outcome is Outcome.PARTIAL else EXIT_COMPLETE

    def summary_lines(self, stopped: bool = False) -> list[str]:
        """The summary: the outcome, then every problem on one line, none left
        out (a problem's detail lines were printed with its warning), then one
        line counting the notes (``to_json`` lists them)."""
        outcome = self.outcome(stopped)
        count = len(self.problems)
        head = f"{outcome.value}: {count} problem(s)" if count else outcome.value
        if any(p.kind is ProblemKind.LVKIT_ERROR for p in self.problems):
            head += " (includes an lvkit error)"
        elif any(p.kind is ProblemKind.ENVIRONMENT for p in self.problems):
            head += " (includes an environment problem)"
        lines = [head, *(f"  {problem.line}" for problem in self.problems)]
        for kind, phrase in _NOTE_PHRASES.items():
            count = sum(1 for note in self.notes if note.kind is kind)
            if count:
                lines.append(f"{count} {phrase} (--format json lists them)")
        return lines

    def print_summary(
        self,
        stopped: bool = False,
        stream: TextIO | None = None,
    ) -> None:
        out = stream or sys.stderr
        for text in self.summary_lines(stopped):
            print(text, file=out)

    def to_json(self, stopped: bool = False) -> dict[str, object]:
        """The same summary as data: the outcome and the full list, and the
        notes when there are any."""
        data: dict[str, object] = {
            "status": self.outcome(stopped).value,
            "problems": [p.to_json() for p in self.problems],
        }
        if self.notes:
            data["notes"] = [p.to_json() for p in self.notes]
        return data


class RunProblems:
    """What one run found, projected from the graphs it used."""

    def __init__(self, settings: RunSettings | None = None) -> None:
        self.settings = settings or RunSettings()
        self._found: list[Problem] = []
        # The graphs this run built to keep (``new_graph``): the report reads their
        # nodes, so a run that stops partway still lists what was recorded.
        self._graphs: list[InMemoryVIGraph] = []
        # What the command prints when a fail-fast stop ends it (set by a command
        # whose result is a data line, e.g. ``index``).
        self.on_stop: Callable[[RunReport], None] | None = None

    def new_graph(self, tracked: bool = True) -> InMemoryVIGraph:
        """A graph whose problems are announced per this run's settings. A tracked
        graph is part of the run's report for as long as the run lives; an
        untracked one (one of many short-lived graphs) is ``collect``ed by hand."""
        # Imported here: the graph is the heavy engine, and a command that is
        # served from the output cache must not pay for it (see
        # tests/test_import_startup.py).
        from .graph.core import InMemoryVIGraph

        graph = InMemoryVIGraph(self.settings)
        if tracked:
            self._graphs.append(graph)
        return graph

    def collect(
        self, graph: InMemoryVIGraph, vi_keys: Iterable[str] | None = None
    ) -> None:
        """Take the problems on the nodes of ``vi_keys`` (every node when None)."""
        self._found.extend(graph.problems(vi_keys))

    def add(self, problem: Problem) -> None:
        """Take a problem another run found, announcing it as this run's."""
        self._found.append(problem)
        self.settings.announce(problem)

    def take(self, problems: Iterable[Problem], announce: bool) -> None:
        """Take the problems a cached output layer returns: ``announce`` is False
        for problems found by a build this run made (already announced as they
        were recorded) and True for those stored with a cached output."""
        for problem in problems:
            if announce:
                self.add(problem)
            else:
                self._found.append(problem)

    def fail(
        self,
        path: Path | str,
        stage: ProblemStage,
        exc: Exception,
        kind: ProblemKind | None = None,
        may_stop: bool = False,
    ) -> Problem:
        """The one VI (or file) this run is about failed with ``exc``: take the
        problem and announce it. The file that could not be read is the one the
        error names, else ``path``. ``kind`` overrides the one ``exc`` implies
        (a module collision is ``UNWRITABLE``). The run is ending, so a fail-fast run
        does not raise here unless ``may_stop`` (one VI of many)."""
        if isinstance(exc, UnreadableVIError) and exc.path is not None:
            path = exc.path
        label = self.settings.label(path)
        problem = (
            Problem(vi=label, stage=stage, kind=kind, reason=str(exc))
            if kind is not None
            else problem_for_exception(label, stage, exc)
        )
        self._found.append(problem)
        self.settings.announce(problem, may_stop=may_stop)
        return problem

    def report(self) -> RunReport:
        return RunReport.of(
            [*self._found, *(p for graph in self._graphs for p in graph.problems())]
        )
