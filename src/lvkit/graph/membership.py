"""Class and library membership: the container's list and the member's claim.

A ``.lvclass`` or ``.lvlib`` lists its members by path; that list is the source
of truth (``docs/_internal/design/labview-binary-format.md``, "Membership"; NI's
error "This VI claims to be part of a library, but that library does not claim
to own this VI"). A
VI also records, in its own ``VILB`` record, the class or library it says it is
in. The graph keeps both, by path:

- ``owns`` (container -> member): the container's file lists the member. This
  is membership; every ownership query reads it.
- ``claimed_owner`` (a VI node attribute: the container's path): the member's
  own record names the container. It never makes a VI a member, and it is not a
  dependency (a VI's ``uses`` are the files it calls and whose types it uses);
  it is how a one-sided claim is found (:meth:`MembershipMixin.claimed_owner`).

A container records its list when it loads, in every load mode; a VI records
its claim when it loads. :meth:`MembershipMixin._link_membership` is the one
place the ``owns`` edge is added and the claim compared with it, for every
pair whose nodes are both in the graph, whichever loaded first, and the one
place a one-sided membership becomes a note on the VI: a VI that claims a
container whose file does not list it (LabVIEW's "This VI claims to be part
of a library, but that library does not claim to own this VI"), or a VI a container
lists that claims another owner.

:meth:`MembershipMixin.class_methods` is a class's method list for every view:
its own methods (what its file lists) and the public and protected methods of
its ancestors, each marked as its own, an override, or inherited.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from ..parser.models import ParsedDependencyRef
from ..run_report import Problem, ProblemKind, ProblemStage
from .models import MethodOverrideInfo
from .node_kinds import EdgeRel

if TYPE_CHECKING:
    import networkx as nx

    from ..structure import LVMethod

# The node attribute holding a container's list: member path key -> the
# member's class-method item (None for a library member).
MEMBERS_ATTR = "members"
# The node attribute holding the container a loaded VI's own record claims
# (its path key; None when the VI records none).
CLAIM_ATTR = "claimed_owner"

# The scopes a child class sees of its ancestors' methods.
_INHERITED_SCOPES = frozenset({"public", "protected"})


@dataclass(frozen=True)
class ListedMethod:
    """What a class's file records for one of its methods (its ``Item``)."""

    scope: str
    is_static: bool
    is_accessor: bool
    accessor_field: str | None
    must_override: bool
    must_call_parent: bool

    @classmethod
    def from_item(cls, method: LVMethod) -> ListedMethod:
        return cls(
            scope=method.scope,
            is_static=method.is_static,
            is_accessor=method.property_name is not None,
            accessor_field=method.property_name,
            must_override=method.must_override,
            must_call_parent=method.must_call_parent,
        )


class MethodOrigin(Enum):
    """Where a method in a class's method list comes from."""

    OWN = "own"
    OVERRIDE = "overrides"
    INHERITED = "inherited from"


@dataclass(frozen=True)
class ClassMethod:
    """One method of a class: the file (``key``), what its listing class
    records for it, where it comes from, and the ancestor it overrides or is
    inherited from (None for an own method that overrides nothing)."""

    key: str
    name: str
    listed: ListedMethod
    origin: MethodOrigin
    ancestor: str | None


class MembershipMixin:
    """Membership edges and problems, and the class method list."""

    if TYPE_CHECKING:
        _dep_graph: nx.DiGraph
        _stubs: set[str]
        _loaded_vis: set[str]
        _listed_by: dict[str, set[str]]
        _claimants: dict[str, set[str]]

        def _add_ownership_edge(
            self, owner: str, member: str, **attrs: object
        ) -> None: ...
        def recorded_candidates(
            self, dep_ref: ParsedDependencyRef, caller_file: Path
        ) -> list[Path]: ...
        def _locate_recorded(self, candidate: Path, leaf: str) -> Path | None: ...
        def _parent_class_key(self, node_key: str) -> str | None: ...
        def record_problem(
            self, vi_key: str, problem: Problem, node_type: object = ...,
            may_stop: bool = ...,
        ) -> None: ...
        def vi_label(self, path: Path | str) -> str: ...
        def key_label(self, key: str) -> str: ...
        def vi_display_name(self, key: str) -> str: ...
        def list_class_keys(self) -> list[str]: ...
        def _owning_class_key(self, vi_key: str) -> str | None: ...
        def _class_node_key(self, key: str) -> str | None: ...

        _vi_nodes: dict[str, set[str]]

    # ---- recording ------------------------------------------------------

    def _record_members(
        self, container_key: str, members: dict[str, ListedMethod | None]
    ) -> None:
        """Record the members the container at ``container_key`` lists (member
        path key -> its class-method item, None for a library member) and link
        those already in the graph."""
        self._dep_graph.nodes[container_key][MEMBERS_ATTR] = dict(members)
        for member in members:
            self._listed_by.setdefault(member, set()).add(container_key)
        self._link_membership(container_key)

    def _record_claim(
        self, vi_key: str, own_record: ParsedDependencyRef | None, vi_file: Path
    ) -> None:
        """Record the container the loaded VI at ``vi_key`` claims in its own
        ``VILB`` record, and link it when that container is in the graph. The
        container is the file at the path the record names, found as any
        recorded file is (``_locate_recorded``: a file name differing only in
        case is that file, as LabVIEW's names are), else that intended path."""
        claimed = None
        if own_record is not None:
            candidates = self.recorded_candidates(own_record, vi_file)
            found = next(
                (
                    hit
                    for c in candidates
                    if (hit := self._locate_recorded(c, own_record.name)) is not None
                ),
                candidates[0] if candidates else None,
            )
            claimed = str(found.resolve()) if found is not None else None
        self._dep_graph.nodes[vi_key][CLAIM_ATTR] = claimed
        if claimed is not None:
            self._claimants.setdefault(claimed, set()).add(vi_key)
        self._link_membership(vi_key)

    # ---- linking --------------------------------------------------------

    def _link_membership(self, key: str) -> None:
        """Add the ``owns`` edge of every container / member pair ``key`` is
        part of whose nodes are both in the graph, and record a one-sided
        membership as a note on the VI. The one place ``owns`` is added."""
        graph = self._dep_graph
        pairs: set[tuple[str, str]] = set()
        pairs.update((c, key) for c in self._listed_by.get(key, ()))
        pairs.update((key, m) for m in graph.nodes[key].get(MEMBERS_ATTR) or {})
        claimed = graph.nodes[key].get(CLAIM_ATTR)
        if claimed is not None:
            pairs.add((claimed, key))
        pairs.update((key, m) for m in self._claimants.get(key, ()))
        for container, member in sorted(pairs):
            if graph.has_node(container) and graph.has_node(member):
                self._link_pair(container, member)

    def _link_pair(self, container: str, member: str) -> None:
        graph = self._dep_graph
        members = graph.nodes[container].get(MEMBERS_ATTR)
        listed = members is not None and member in members
        claimed = graph.nodes[member].get(CLAIM_ATTR) == container
        if members is not None and listed:
            edge = graph.get_edge_data(container, member) or {}
            if edge.get("rel") != EdgeRel.OWNS:
                item = members[member]
                attrs = _edge_attrs(item) if item is not None else {}
                self._add_ownership_edge(container, member, **attrs)
        # A one-sided membership is known only once both files are read: the
        # container's list (``members``) and the VI's own claim.
        if members is None or CLAIM_ATTR not in graph.nodes[member]:
            return
        if claimed and not listed:
            self._membership_note(
                member,
                f"this VI claims to be part of {self.key_label(container)}, but "
                f"that {_kind(container)} does not claim to own this VI: it is "
                "not a member",
            )
        elif listed and not claimed:
            own = graph.nodes[member].get(CLAIM_ATTR)
            claims = (
                f"claims to be part of {self.key_label(own)}"
                if own is not None
                else "claims no owner"
            )
            self._membership_note(
                member,
                f"{self.key_label(container)} lists this VI as its member, but "
                f"the VI {claims}",
            )

    def _membership_note(self, vi_key: str, reason: str) -> None:
        self.record_problem(
            vi_key,
            Problem(
                vi=self.key_label(vi_key),
                stage=ProblemStage.LOAD,
                kind=ProblemKind.MEMBERSHIP,
                reason=reason,
            ),
            may_stop=False,
        )

    def claimed_owner(self, vi_key: str) -> str | None:
        """The path key of the class or library the loaded VI at ``vi_key``
        says it is in (its own ``VILB`` record), whether or not that container
        lists it; None when it claims none or is not loaded."""
        if not self._dep_graph.has_node(vi_key):
            return None
        claimed = self._dep_graph.nodes[vi_key].get(CLAIM_ATTR)
        return str(claimed) if claimed is not None else None

    # ---- the class method list -------------------------------------------

    def listed_members(self, container_key: str) -> dict[str, ListedMethod | None]:
        """The members the container at ``container_key`` lists (path key ->
        its class-method item, None for a library member), in its file's order;
        empty when it is not loaded."""
        if not self._dep_graph.has_node(container_key):
            return {}
        return dict(self._dep_graph.nodes[container_key].get(MEMBERS_ATTR) or {})

    def method_name(self, key: str) -> str:
        """The file name of the member at ``key``: its node's name when it is in
        the graph, else the last part of its path."""
        if self._dep_graph.has_node(key):
            name = self._dep_graph.nodes[key].get("name")
            if name:
                return str(name)
        return Path(key).name

    def _listed_methods(self, class_key: str) -> list[tuple[str, ListedMethod]]:
        """The VI members the class at ``class_key`` lists, with their items."""
        return [
            (key, item)
            for key, item in self.listed_members(class_key).items()
            if item is not None
        ]

    def class_ancestor_keys(self, class_key: str) -> list[str]:
        """The keys of the loaded ancestors of the class at ``class_key``,
        nearest first."""
        chain: list[str] = []
        parent = self._parent_class_key(class_key)
        while parent is not None and parent not in chain and parent != class_key:
            chain.append(parent)
            parent = self._parent_class_key(parent)
        return chain

    def overridden_method(
        self, class_key: str, name: str
    ) -> tuple[str, str] | None:
        """``(ancestor key, method key)`` of the nearest ancestor's dynamic
        method named ``name`` (a method file's name, compared as LabVIEW
        compares names, without regard to case), or None."""
        folded = name.casefold()
        for ancestor in self.class_ancestor_keys(class_key):
            for key, item in self._listed_methods(ancestor):
                if not item.is_static and self.method_name(key).casefold() == folded:
                    return ancestor, key
        return None

    def class_methods(self, class_key: str) -> list[ClassMethod]:
        """The methods of the class at ``class_key``: the ones its file lists
        (an own dynamic method of the same name as an ancestor's dynamic method
        overrides the nearest such ancestor's), then the public and protected
        methods of its ancestors, nearest first, that no nearer class has."""
        methods: list[ClassMethod] = []
        seen: set[str] = set()
        for key, item in self._listed_methods(class_key):
            name = self.method_name(key)
            found = None if item.is_static else self.overridden_method(class_key, name)
            methods.append(
                ClassMethod(
                    key=key,
                    name=name,
                    listed=item,
                    origin=MethodOrigin.OVERRIDE if found else MethodOrigin.OWN,
                    ancestor=found[0] if found else None,
                )
            )
            seen.add(name.casefold())
        for ancestor in self.class_ancestor_keys(class_key):
            for key, item in self._listed_methods(ancestor):
                name = self.method_name(key)
                if item.scope not in _INHERITED_SCOPES or name.casefold() in seen:
                    continue
                methods.append(
                    ClassMethod(
                        key=key,
                        name=name,
                        listed=item,
                        origin=MethodOrigin.INHERITED,
                        ancestor=ancestor,
                    )
                )
                seen.add(name.casefold())
        return methods

    def get_method_overrides(self, vi_name: str) -> MethodOverrideInfo | None:
        """The loaded method VIs this class method VI overrides and is
        overridden by (``class_methods``): the nearest ancestor's dynamic method
        of its name, and each loaded class's dynamic method of its name whose
        nearest such ancestor method is this one. None when it is not a method
        its class lists, or has neither.
        """
        owner_key = self._owning_class_key(vi_name)
        if owner_key is None or self._class_node_key(owner_key) is None:
            return None
        name = self.method_name(vi_name)
        found = self.overridden_method(owner_key, name)
        overrides = (
            found[1] if found is not None and found[1] in self._vi_nodes else None
        )
        overridden_by = sorted(
            (
                key
                for class_key in self.list_class_keys()
                for key, item in self.listed_members(class_key).items()
                if item is not None
                and not item.is_static
                and key in self._vi_nodes
                and self.method_name(key).casefold() == name.casefold()
                and self.overridden_method(class_key, name) == (owner_key, vi_name)
            ),
            key=lambda key: (self.vi_display_name(key), key),
        )

        if overrides is None and not overridden_by:
            return None

        return MethodOverrideInfo(
            vi_name=vi_name,
            overrides=overrides,
            overridden_by=overridden_by,
        )


def _edge_attrs(item: ListedMethod) -> dict[str, object]:
    """A class method's item as its ``owns`` edge attributes."""
    return {
        "scope": item.scope,
        "is_static": item.is_static,
        "is_accessor": item.is_accessor,
        "accessor_field": item.accessor_field,
        "must_override": item.must_override,
        "must_call_parent": item.must_call_parent,
    }


def _kind(container: str) -> str:
    return "class" if container.lower().endswith(".lvclass") else "library"
