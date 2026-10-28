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


class LoadProblemsMixin:
    """The graph's way of recording a problem on a VI's node."""

    run_settings: RunSettings
    _dep_graph: nx.DiGraph
    _vilib_root: Path | None
    _userlib_root: Path | None
    _instrlib_root: Path | None

    if TYPE_CHECKING:

        def load_vi(
            self,
            vi_path: Path | str,
            mode: LoadMode = ...,
            search_paths: list[Path] | None = ...,
            clear_first: bool = ...,
            layout: bool = ...,
        ) -> str | None: ...
        def resolve_vi_name(self, name: str) -> str: ...
        def _add_stub(
            self,
            key: str,
            node_type: NodeType,
            name: str,
            qname: str | None,
            path_tokens: list[str] | None,
            caller: str | None,
        ) -> None: ...

    def library_root_of(self, path: Path) -> tuple[str, Path] | None:
        """The LabVIEW library folder (``<vilib>``, ``<userlib>``, ``<instrlib>``)
        the file at ``path`` is in, with that folder, else None."""
        for token, root in (
            ("<vilib>", self._vilib_root),
            ("<userlib>", self._userlib_root),
            ("<instrlib>", self._instrlib_root),
        ):
            if root is not None and path.resolve().is_relative_to(root.resolve()):
                return token, root
        return None

    def vi_label(self, path: Path | str) -> str:
        """How a user sees a VI file (see ``RunSettings.label``)."""
        return self.run_settings.label(path)

    def key_label(self, key: str) -> str:
        """How a user sees a graph key: a file path as ``vi_label`` shows it, a
        qualified name (a VI that is not a file) as it is."""
        return self.vi_label(key) if Path(key).is_absolute() else key

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
        for work that is best-effort)."""
        if vi_key not in self._dep_graph:
            try:
                vi_key = self.resolve_vi_name(vi_key)
            except (KeyError, ValueError):
                pass
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
        """The problems on the nodes of ``vi_keys`` (every node when None)."""
        keys = list(self._dep_graph.nodes) if vi_keys is None else list(vi_keys)
        found: list[Problem] = []
        for key in keys:
            if key in self._dep_graph:
                found.extend(self._dep_graph.nodes[key].get(PROBLEMS_ATTR, ()))
        return found

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
        self.record_file_problem(
            vi_path,
            problem_for_exception(self.vi_label(vi_path), stage, exc),
            node_type,
        )

    def load_vi_recording(
        self,
        vi_path: Path | str,
        mode: LoadMode,
        search_paths: list[Path] | None = None,
    ) -> str | None:
        """``load_vi`` for one VI of many: a failure is recorded on the VI's node
        and gives None, so the VIs after it still load."""
        try:
            return self.load_vi(vi_path, mode, search_paths)
        except Exception as exc:
            self.record_load_failure(vi_path, exc)
            return None
