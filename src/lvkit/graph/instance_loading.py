"""Loading the calls of a VI that run an instance saved in the caller's own file.

LabVIEW saves, in the caller's file, a private copy of every malleable VI (``.vim``)
and Express VI the caller calls, adapted to that call: its controls have the types
wired to the call and each Type Specialization Structure keeps the frame chosen for
them. The call runs that copy. The loader makes it a dependency of the caller
under the key ``<caller key>/<instance name>`` (``InMemoryVIGraph.embedding_vi``),
loaded like any VI, and keeps the ``.vim`` it is an instance of on the call
(``VINode.instance_of_key``) so every view names the call as a call of the ``.vim``.

A ``.vim`` or Express VI the vi.lib catalog gives Python for is not loaded as an
instance: the catalog's Python is what every call of it runs.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from ..embedded_instances import CallerInstances, EmbeddedInstance
from ..load_mode import LoadMode
from ..run_report import ProblemStage, problem_for_exception

if TYPE_CHECKING:
    import networkx as nx

    from ..parser.models import ParsedDependencyRef, ParsedVI
    from ..run_report import Problem
    from ..vilib_resolver import VILibResolver
    from .node_kinds import NodeType
    from .recorded_paths import RecordedPaths


@dataclass(frozen=True)
class InstanceCallLoad:
    """What loading one embedded-instance call found.

    ``key`` is the dependency the call runs: its saved instance, else the ``.vim``
    (``None`` when that was not found). ``saved_instance`` is whether ``key`` is
    the instance. ``instance_of`` is the key of the ``.vim`` the instance is an
    instance of, when it is one and that file is found. ``unreadable`` is why the
    call's saved instance could not be read, when it could not. ``source`` and
    ``source_name`` are the ``.vim``'s recorded path and its display name, when the
    call names one."""

    key: str | None
    saved_instance: bool = False
    instance_of: str | None = None
    unreadable: str | None = None
    source: ParsedDependencyRef | None = None
    source_name: str | None = None


class InstanceLoadingMixin:
    """The part of the loader that loads a caller's saved instances."""

    _dep_graph: nx.DiGraph
    _recorded: RecordedPaths
    _vi_display_names: dict[str, str]
    vilib_catalog: VILibResolver

    if TYPE_CHECKING:

        def catalog_path_of(
            self, interface_key: str | None, qualified_path: str | None, caller: str
        ) -> str | None: ...
        def _load_instance_dependency(
            self,
            source: ParsedDependencyRef,
            caller_file: Path,
            caller_qname: str | None,
            mode: LoadMode,
            resolved_paths: dict[str, Path | None] | None = None,
        ) -> tuple[str, str | None]: ...
        def _load_vi_recursive(
            self,
            bd_xml: Path,
            fp_xml: Path | None,
            main_xml: Path | None,
            visited: set[str],
            *,
            source_dir: Path | None = None,
            source_override: Path | None = None,
            mode: LoadMode = ...,
            preparsed: ParsedVI | None = None,
        ) -> str | None: ...
        def _register_dep_name(self, key: str, name: str, qname: str) -> None: ...
        def _note_recorded_path(
            self,
            key: str,
            dep_ref: ParsedDependencyRef | None,
            qualified_name: str,
            caller_key: str | None,
        ) -> None: ...
        def record_problem(
            self,
            vi_key: str,
            problem: Problem,
            node_type: NodeType = ...,
            may_stop: bool = True,
        ) -> None: ...
        def vi_label(self, path: Path | str) -> str: ...

    def _load_instance_call(
        self,
        qname: str,
        embedded: CallerInstances,
        recorded: ParsedDependencyRef | None,
        caller_key: str,
        caller_file: Path,
        mode: LoadMode,
        resolved_paths: dict[str, Path | None] | None,
    ) -> InstanceCallLoad | None:
        """Load the dependency of the call the caller records as ``qname``, when it
        is an embedded instance; None for any other call, and for an Express VI the
        catalog gives Python for (an ordinary call).

        ``recorded`` is the path the caller records for the call. A call whose
        instance is not in the catalog runs the instance, loaded as a dependency of
        the caller; its ``.vim``, when it has one, is loaded as well, for every
        view that names the call. A call whose instance cannot be read is recorded
        as a problem on the caller and runs its ``.vim`` as an ordinary call, with
        ``unreadable`` saying why."""
        name = qname.rsplit(":", 1)[-1]
        source = embedded.sources.get(name)
        instance = embedded.instances.get(name)
        failure = embedded.failures.get(name)
        if source is None and instance is None and failure is None:
            return None
        source_name: str | None = None
        source_key: str | None = None
        if source is not None:
            source_name, source_key = self._load_instance_dependency(
                source,
                caller_file,
                caller_qname=caller_key,
                mode=mode,
                resolved_paths=resolved_paths,
            )
        named = source or recorded
        if named is not None and self.vilib_catalog.gives_code(
            self.catalog_path_of(source_key, "/".join(named.path_tokens), caller_key)
        ):
            if source is None:
                return None
            return InstanceCallLoad(source_key, source=source, source_name=source_name)
        if instance is not None:
            try:
                vim_path = (
                    self.catalog_path_of(
                        source_key, "/".join(source.path_tokens), caller_key
                    )
                    if source is not None
                    else None
                )
                key = self._load_embedded_instance(
                    instance,
                    caller_key,
                    mode,
                    qname,
                    recorded,
                    source_name,
                    source_key,
                    vim_path,
                )
            except Exception as exc:
                failure = exc
            else:
                return InstanceCallLoad(
                    key,
                    saved_instance=True,
                    instance_of=source_key,
                    source=source,
                    source_name=source_name,
                )
        reason = None
        if failure is not None:
            reason = self._record_unreadable_instance(caller_key, name, source, failure)
        if source is None:
            return None
        return InstanceCallLoad(
            source_key, unreadable=reason, source=source, source_name=source_name
        )

    def _load_embedded_instance(
        self,
        instance: EmbeddedInstance,
        holder_key: str,
        mode: LoadMode,
        qualified_name: str,
        recorded: ParsedDependencyRef | None,
        vim_name: str | None,
        vim_key: str | None,
        vim_path: str | None,
    ) -> str | None:
        """Load the VI an embedded instance is, as a dependency of the VI that
        holds it. The instance has no file of its own: its key is
        ``<holder key>/<instance name>`` (``InMemoryVIGraph.embedding_vi``), and the
        paths its own link table records are relative to that place, one level
        inside the holder. ``qualified_name`` and ``recorded`` are the name and
        path the holder records for the instance; ``vim_name`` is the qualified name
        of the ``.vim`` it is an instance of, when it is (found or not), and
        ``vim_key`` that file's key (``InMemoryVIGraph.instance_file``) and
        ``vim_path`` the path the holder records for it
        (``InMemoryVIGraph.instance_path``). Returns the key; an instance that
        cannot be loaded raises."""
        place = Path(holder_key) / instance.name
        key = str(place.resolve())
        # The place is known before the instance's own dependencies load, so the
        # paths they record resolve against the holder's.
        self._dep_graph.add_node(key, embedded_in=holder_key)
        self._recorded.owners_changed()
        loaded = self._load_vi_recursive(
            instance.bd_xml,
            instance.fp_xml,
            instance.main_xml,
            visited=set(),
            source_dir=Path(holder_key),
            source_override=place,
            mode=LoadMode.FULL if mode is LoadMode.FULL else LoadMode.NONE,
            preparsed=instance.parsed,
        )
        if loaded is None:
            return None
        self._dep_graph.add_edge(holder_key, loaded)
        if vim_name is not None:
            self._dep_graph.nodes[loaded]["instance_of"] = vim_name
            if vim_key is not None:
                self._dep_graph.nodes[loaded]["instance_file"] = vim_key
            if vim_path is not None:
                self._dep_graph.nodes[loaded]["instance_path"] = vim_path
        # The holder names the instance as it records it, and stores its path.
        self._register_dep_name(loaded, instance.name, qualified_name)
        # A copy is the VI it was made from as a VI (its qualified name); a copy
        # that records none, as LabVIEW records it, inside the VI that holds it.
        if loaded in self._vi_display_names:
            holder_name = self._vi_display_names.get(holder_key, Path(holder_key).name)
            self._vi_display_names[loaded] = (
                vim_name if vim_name is not None else f"{holder_name}:{qualified_name}"
            )
        self._note_recorded_path(loaded, recorded, qualified_name, holder_key)
        return loaded

    def _record_unreadable_instance(
        self,
        holder_key: str,
        instance_name: str,
        source: ParsedDependencyRef | None,
        exc: Exception,
    ) -> str:
        """Record, on the VI that holds it, an embedded instance lvkit could not
        read; returns the reason, which the call that runs the instance carries as
        its gap."""
        problem = problem_for_exception(
            self.vi_label(Path(holder_key)), ProblemStage.EXTRACT, exc, holder_key
        )
        of = f" of {source.qualified_name or source.name}" if source else ""
        reason = (
            "the copy of the VI saved in this VI's own file for this call "
            f"(instance {instance_name}{of}) could not be read: {problem.reason}"
        )
        self.record_problem(holder_key, replace(problem, reason=reason))
        return reason
