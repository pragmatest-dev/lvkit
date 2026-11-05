"""Problems are data on the graph: a VI that cannot be read is its own VI node (a
stub named by its path, like any VI whose file is absent) carrying the problems
found with it, and the load goes on.

A problem found while projecting a loaded VI (an lvkit error in a view) is
recorded on that VI's node the same way. A command projects the problems of the
VIs it used with ``problems``; a VI that is loaded successfully later loses the
problems it had.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx

from ..run_report import (
    Problem,
    ProblemStage,
    RunSettings,
    UnreadableVIError,
    problem_for_exception,
)
from .node_kinds import NodeType

if TYPE_CHECKING:
    from ..load_mode import LoadMode

# The dependency-node attribute holding a VI's problems: a tuple of ``Problem``.
PROBLEMS_ATTR = "problems"
# The dependency-node attribute of a method stub whose class file is known but the
# method's own file is not determined: ``(class file, method file name)``.
CLASS_MEMBER_ATTR = "class_member"


class LoadProblemsMixin:
    """The graph's way of recording a problem on a VI's node."""

    run_settings: RunSettings
    _dep_graph: nx.DiGraph

    if TYPE_CHECKING:

        def recorded_path(self, key: str) -> str | None: ...
        def load_vi(
            self,
            vi_path: Path | str,
            mode: LoadMode = ...,
            clear_first: bool = ...,
            layout: bool = ...,
            original_path: Path | str | None = ...,
        ) -> str | None: ...
        def _add_stub(
            self,
            key: str,
            node_type: NodeType,
            name: str,
            qname: str | None,
            path_tokens: list[str] | None,
            caller: str | None,
        ) -> None: ...

    def vi_label(self, path: Path | str) -> str:
        """How a user sees a VI file (see ``RunSettings.label``), by the
        installation path its callers recorded when they did."""
        return self.run_settings.label(path, self.recorded_path(str(path)))

    def key_label(self, key: str) -> str:
        """How a user sees a graph key: a file path as ``vi_label`` shows it; a
        method of a class whose file is known but undetermined
        (``CLASS_MEMBER_ATTR``) as the class's label and the method's file name;
        another VI that is not a file (a class method no class lists) by the
        qualified name recorded for it, else the key as it is."""
        if Path(key).is_absolute():
            return self.vi_label(key)
        if key in self._dep_graph:
            member = self._dep_graph.nodes[key].get(CLASS_MEMBER_ATTR)
            if member is not None:
                class_file, leaf = member
                return f"{self.vi_label(class_file)}:{leaf}"
            qname = self._dep_graph.nodes[key].get("qname")
            if qname:
                return str(qname)
        return key

    def record_problem(
        self,
        vi_key: str,
        problem: Problem,
        node_type: NodeType = NodeType.VI,
        may_stop: bool = True,
    ) -> None:
        """Record ``problem`` on the node of ``vi_key`` (a file the graph has no
        node for gets a stub node of ``node_type``), once; announce it per the
        run's settings (a fail-fast run stops here unless ``may_stop`` is False,
        for work that is best-effort). ``vi_key`` is an identity: it is never
        resolved by name, so a problem never lands on another VI of the same
        file name."""
        if problem.key is None:
            problem = replace(problem, key=vi_key)
        if vi_key not in self._dep_graph:
            self._add_stub(vi_key, node_type, Path(vi_key).name, None, None, None)
        held: tuple[Problem, ...] = self._dep_graph.nodes[vi_key].get(
            PROBLEMS_ATTR, ()
        )
        if problem in held:
            return
        self._dep_graph.nodes[vi_key][PROBLEMS_ATTR] = (*held, problem)
        self.run_settings.announce(problem, may_stop)

    def record_file_problem(
        self,
        path: Path | str,
        problem: Problem,
        node_type: NodeType = NodeType.VI,
        may_stop: bool = True,
    ) -> None:
        """``record_problem`` for the file at ``path`` (its node is keyed by the
        resolved path)."""
        self.record_problem(str(Path(path).resolve()), problem, node_type, may_stop)

    def clear_problems(self, vi_key: str) -> None:
        """Forget the problems of a VI that has just been loaded successfully."""
        if vi_key in self._dep_graph:
            self._dep_graph.nodes[vi_key].pop(PROBLEMS_ATTR, None)

    def problems(self, vi_keys: Iterable[str] | None = None) -> list[Problem]:
        """The problems on the nodes of ``vi_keys`` (every node when None), each
        VI named from the graph as it is now (``labelled``)."""
        keys = list(self._dep_graph.nodes) if vi_keys is None else list(vi_keys)
        found: list[Problem] = []
        for key in keys:
            if key in self._dep_graph:
                held = self._dep_graph.nodes[key].get(PROBLEMS_ATTR, ())
                found.extend(self.labelled(p) for p in held)
        return found

    def has_key(self, key: str) -> bool:
        """Whether ``key`` is a node of the graph."""
        return key in self._dep_graph

    def labelled(self, problem: Problem) -> Problem:
        """``problem`` with its VI named as the graph names its key now
        (``key_label``): the one place a problem's VI gets the name every view
        shows, once the callers that record its installation path are loaded.
        A problem with no key keeps its name."""
        if problem.key is None or problem.key not in self._dep_graph:
            return problem
        return replace(problem, vi=self.key_label(problem.key))

    def record_load_failure(
        self,
        vi_path: Path | str,
        exc: Exception,
        stage: ProblemStage | None = None,
        node_type: NodeType = NodeType.VI,
    ) -> None:
        """Record that loading ``vi_path`` failed with ``exc``: an unreadable file
        (the extract stage) or an lvkit error (the load stage)."""
        if stage is None:
            stage = (
                ProblemStage.EXTRACT
                if isinstance(exc, UnreadableVIError)
                else ProblemStage.LOAD
            )
        key = str(Path(vi_path).resolve())
        self.record_file_problem(
            vi_path,
            problem_for_exception(self.vi_label(vi_path), stage, exc, key),
            node_type,
        )

    def load_vi_recording(
        self,
        vi_path: Path | str,
        mode: LoadMode,
    ) -> str | None:
        """``load_vi`` for one VI of many: a failure is recorded on the VI's node
        and gives None, so the VIs after it still load."""
        try:
            return self.load_vi(vi_path, mode)
        except Exception as exc:
            self.record_load_failure(vi_path, exc)
            return None
