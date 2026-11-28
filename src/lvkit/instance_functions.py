"""The functions that run the instances saved in callers' files, where they live and
what each is called.

LabVIEW saves, in each caller's file, a private copy of every malleable VI
(``.vim``) and Express VI the caller calls, adapted to that call (see
``graph/instance_loading.py``). A call of one is an ordinary function call; the
copies are written as functions in one module per ``.vim``:

* The module is the ``.vim``'s own (where its source would be generated, as
  ``ModuleLayout`` places it). A copy whose ``.vim`` file is not found, and an
  Express VI's copy, has no such file: its module is beside the VI that holds it,
  named from the name its call shows (``InstanceHome.file_name``), one for every
  copy of that name in that VI.
* Copies whose generated functions have the same body share one function
  (``normalised_body`` says what the body is).
* A shared function that holds state (an uninitialized shift register, a Feedback
  Node, a local variable indicator, Elapsed Time: anything that adds a
  module-level global) and is called from more than one call site is a problem
  of the run (``state_problem``): LabVIEW inlines a ``.vim`` with Preallocated
  clone reentrant execution, so every call site has its own data (NI, "Building
  Malleable VIs": ``building-malleable-vis.html``), but the function holds one
  copy of the state, so its callers share it.
* The group of the copy held by the VI whose path sorts first takes the ``.vim``'s
  own function name (``from_json_text``); the others number from 2 in the order of
  their first VI's path (``from_json_text_2``).

A caller imports the function its copy maps to and calls it as any VI.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from lvkit.codegen.ast_utils import numbered_name, to_function_name
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import VINode
from lvkit.library_folders import is_symbolic_root
from lvkit.run_report import Problem, ProblemKind, ProblemStage

# What a trial build gives for one copy: its body (``normalised_body``) and what
# holds state in its function, in LabVIEW's words (empty when nothing does).
TrialBody = tuple[str, tuple[str, ...]]

_GUID = re.compile(
    r"[0-9a-f]{8}[-_][0-9a-f]{4}[-_][0-9a-f]{4}[-_][0-9a-f]{4}[-_][0-9a-f]{12}"
)


def normalised_body(code: str, function_name: str, labels: Iterable[str]) -> str:
    """The text of a copy's generated module that is its function, so two copies
    run the same code exactly when their bodies are equal. Dropped: the lines that
    describe the copy's front panel and window (every ``<function>.<attribute> =
    ...`` line: ``panel``, ``window_title``, ``window_bounds``, ``vi_name``,
    ``tags``, ...), each of ``labels`` (the copy's name as a message shows it) and
    the GUID of the copy's own name, which the text of a gap may carry.
    ``function_name`` is the name the function and its result class were written
    under, the same for every copy compared."""
    kept = [
        line for line in code.splitlines() if not line.startswith(f"{function_name}.")
    ]
    text = "\n".join(kept)
    for label in labels:
        text = text.replace(label, "<copy>")
    return _GUID.sub("<copy>", text)


@dataclass(frozen=True)
class InstanceHome:
    """One module that holds the functions of the saved copies of one VI.

    ``key`` is the module's owner: the ``.vim`` file (``file_name`` is None), else
    the first of the copies. A ``.vim`` that is not found but whose callers record
    an installation path (``<vilib>/...``, ``place_path``) has its module there,
    where its file would be; any other copy has its module beside the VI that
    holds it. ``file_name`` is the file name of the VI the copies are copies of.
    ``function`` is the name of the first function; ``copies`` are the keys of the
    saved copies, in key order."""

    key: str
    copies: tuple[str, ...]
    function: str
    file_name: str | None
    place_path: str | None = None


@dataclass(frozen=True)
class InstanceFunction:
    """A function of an ``InstanceHome``: the copy it is written from (the first
    copy of its group), its name, every copy that calls it and what holds state in
    it (see ``TrialBody``)."""

    key: str
    name: str
    copies: tuple[str, ...]
    state: tuple[str, ...] = ()


def _file_name(graph: InMemoryVIGraph, key: str) -> str:
    """The file name of the VI a saved copy is a copy of, as its call shows it:
    the ``.vim`` the call names, else the title of the copy's front panel window
    (an Express VI's name)."""
    vim = graph.instance_vim(key)
    if vim:
        return vim.rsplit(":", 1)[-1]
    title = graph.get_vi_properties(key).window.window_title
    return f"{title or 'Saved Instance'}.vi"


def called_as_files(graph: InMemoryVIGraph, keys: Iterable[str]) -> set[str]:
    """The keys of the VIs that a call among the VIs ``keys`` runs as the file
    itself: any call but one that runs a saved copy."""
    return {
        node.callee_key
        for key in keys
        for node in graph.iter_nodes(key)
        if isinstance(node, VINode)
        and node.callee_key is not None
        and not node.runs_saved_instance
    }


def instance_homes(
    graph: InMemoryVIGraph, keys: Iterable[str], has_module: Callable[[str], bool]
) -> list[InstanceHome]:
    """The homes of the saved instances among ``keys``, in key order. A copy of a
    ``.vim`` whose file has a module (``has_module``) is in that module; any other
    is in a module beside the VI that holds it, shared by the copies of one name."""
    # The module's owner, the VI file name and the installation path, by home.
    by_home: dict[tuple[str, str | None, str | None], list[str]] = {}
    for key in sorted(keys):
        holder = graph.instance_holder(key)
        if holder is None:
            continue
        vim = graph.instance_file(key)
        path = graph.instance_path(key)
        if vim is not None and has_module(vim):
            by_home.setdefault((vim, None, None), []).append(key)
        elif path is not None and is_symbolic_root(path.split("/")[0]):
            by_home.setdefault(("", _file_name(graph, key), path), []).append(key)
        else:
            by_home.setdefault((holder, _file_name(graph, key), None), []).append(key)
    homes: list[InstanceHome] = []
    for (owner, file_name, path), copies in by_home.items():
        if file_name is None:
            function = to_function_name(Path(owner).name)
            homes.append(InstanceHome(owner, tuple(copies), function, None))
        else:
            function = to_function_name(file_name)
            homes.append(
                InstanceHome(copies[0], tuple(copies), function, file_name, path)
            )
    return homes


class InstanceFunctions:
    """The functions of every ``InstanceHome``, planned when first asked for.

    ``trial`` builds one copy's function under a name and gives its
    ``TrialBody``; planning a home builds each of its copies once to group them.
    A copy that calls another saved copy plans that copy's home first (the call
    asks ``function_name``)."""

    def __init__(
        self,
        graph: InMemoryVIGraph,
        homes: Iterable[InstanceHome],
        holder_path: Callable[[str], str],
        trial: Callable[[str, str], TrialBody],
    ) -> None:
        """``holder_path`` is the path a VI that holds copies is sorted by (the
        one that places its module)."""
        self._graph = graph
        self._homes = {home.key: home for home in homes}
        self._home_of = {
            copy: home for home in self._homes.values() for copy in home.copies
        }
        self._holder_path = holder_path
        self._trial = trial
        self._functions: dict[str, list[InstanceFunction]] = {}
        self._names: dict[str, str] = {}
        self._planning: set[str] = set()

    @property
    def homes(self) -> list[InstanceHome]:
        return list(self._homes.values())

    @property
    def copies(self) -> frozenset[str]:
        """The keys of every saved copy that has a function."""
        return frozenset(self._home_of)

    def home_of(self, key: str) -> InstanceHome | None:
        """The home whose module holds the function of the copy ``key``."""
        return self._home_of.get(key)

    def function_name(self, key: str) -> str | None:
        """The name of the function the copy ``key`` calls; None when ``key`` is
        not a saved copy."""
        home = self._home_of.get(key)
        if home is None:
            return None
        if home.key in self._planning:
            return home.function
        self.functions(home)
        return self._names[key]

    def functions(self, home: InstanceHome) -> list[InstanceFunction]:
        """The functions of ``home``, in the order that names them."""
        if home.key not in self._functions:
            self._plan(home)
        return self._functions[home.key]

    def _plan(self, home: InstanceHome) -> None:
        self._planning.add(home.key)

        def holder_path(copy: str) -> str:
            return self._holder_path(self._graph.instance_holder(copy) or copy)

        groups: list[list[str]] = []
        by_body: dict[str, list[str]] = {}
        state: dict[str, tuple[str, ...]] = {}
        for copy in sorted(home.copies, key=lambda c: (holder_path(c), c)):
            body, holders = self._trial(copy, home.function)
            if body in by_body:
                by_body[body].append(copy)
            else:
                by_body[body] = [copy]
                state[copy] = holders
                groups.append(by_body[body])
        taken: set[str] = set()
        functions: list[InstanceFunction] = []
        for group in groups:
            name = (
                numbered_name(home.function, lambda n: n in taken)
                if taken
                else home.function
            )
            taken.add(name)
            functions.append(
                InstanceFunction(group[0], name, tuple(group), state[group[0]])
            )
            for copy in group:
                self._names[copy] = name
        self._functions[home.key] = functions
        self._planning.discard(home.key)

    def call_sites(self, function: InstanceFunction) -> list[str]:
        """The calls of ``function``, each as ``<VI that holds it>: <call name>``,
        in order."""
        graph = self._graph
        sites = {
            f"{graph.key_label(holder)}: {node.name}"
            for copy in function.copies
            if (holder := graph.instance_holder(copy)) is not None
            for node in graph.iter_nodes(holder)
            if isinstance(node, VINode) and node.callee_key == copy
        }
        return sorted(sites)


def state_problem(
    graph: InMemoryVIGraph,
    key: str,
    shown: str,
    function: InstanceFunction,
    sites: list[str],
) -> Problem:
    """The problem of a function that holds state and runs from several call
    sites: its callers share the state, where LabVIEW gives each call site its own
    (a malleable VI runs as a preallocated clone). ``key`` is the VI it is
    recorded on and ``shown`` the name of the VI the function is a copy of."""
    holders = ", ".join(sorted(set(function.state)))
    return Problem(
        vi=graph.key_label(key),
        key=key,
        stage=ProblemStage.GENERATE,
        kind=ProblemKind.GAP,
        reason=(
            f"{shown} holds state ({holders}) and is called from {len(sites)} "
            "places; LabVIEW gives each call site its own copy of that state "
            "(a malleable VI runs as a preallocated clone), but the generated "
            f"function {function.name} shares one, so the callers share the "
            f"value. Calls: {'; '.join(sites)}"
        ),
        code="shared_state",
    )
