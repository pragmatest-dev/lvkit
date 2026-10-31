"""Which file a class, typedef or library in one VI or control denotes.

A type descriptor stores a class or typedef with no path: a class by its
qualified name (its owners, then its file: ``lv_icon.lvlib:History.lvlib:History
.lvclass``), a typedef by its place in the VI's type table. So does a Shared
Variable record for its library (a name). The file that uses a type also
records, in its link table, each class, typedef and library it uses at a path.
``TypeRecords`` holds those records of one file, each with the dependency key its
path loaded (or would load) under; the loader stores one on each VI's and
control's dependency node, and a type is tied to a record by two rules:

- A typedef is tied by the record's offset list (``tie_typedef``). A ``TDCC``
  record lists the uids of the heap objects that are its typedef's instances;
  each is a ``typeDef`` object whose type id (``LVType.type_id``) is that
  typedef's in this file (``parser/typedef_ties.py``). No name is compared.
- A class is tied by its full qualified name (``tie_class``). A record of a file
  in the caller's own library or class omits the owners the two share and sets
  bit 2 of its link flag (``link_table._read_link_flag``): icon-editor
  ``History/Add Data to History.vi`` (owners ``lv_icon.lvlib``, ``History.lvlib``)
  records ``History.lvclass`` (flag 2) for the type
  ``lv_icon.lvlib:History.lvlib:History.lvclass``; DCAF ``editor node/Add Channel
  to Objects.vi`` (owner ``DAQmx Module editor node.lvclass``) records ``Map.ctl``
  (flag 2) for ``DAQmx Module editor node.lvclass:Map.ctl``. Which owners are left
  out is not recorded, so the name is the caller's first k owners, then the
  record's name, for the k that equals the type's. Names compare without regard
  to case, as LabVIEW's do. Several records that give one name at different paths
  are one file recorded twice: the one on this machine, else the first recorded.

A type the file records nothing for is nested in one of the file's dependencies
(a class inside a typedef's cluster, a class on a subVI's connector pane): it is
the file that dependency's own records give it (``resolve_type``), the nearest
dependencies first. Nothing here searches the graph by name.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from lvkit.models import LVType

# Bit 2 of a link record's flag: the file is in the caller's own library or
# class, and the record's name omits the owners the two share.
_SHARES_OWNERS = 0x2


@dataclass(frozen=True)
class NamedRecord:
    """One link record of a file: the name it gives the file it names, its link
    flag (None when not read), the dependency key of that file, whether the file
    is on this machine, and the type ids of the typedef instances its offset
    list names (``TDCC`` records)."""

    name: str
    link_flag: int | None
    key: str
    exists: bool
    type_ids: tuple[int, ...] = ()

    def is_named(self, name: str, owners: Sequence[str]) -> bool:
        """Whether ``name`` (a class's qualified name) is this record's, in a file
        whose own owner chain is ``owners``."""
        target = name.casefold()
        if target == self.name.casefold():
            return True
        if self.link_flag is not None and not self.link_flag & _SHARES_OWNERS:
            return False
        return any(
            target == ":".join([*owners[:count], self.name]).casefold()
            for count in range(1, len(owners) + 1)
        )


@dataclass
class TypeRecords:
    """The records of one VI or control that name a class, typedef or library,
    its own owner chain, and the keys of the dependencies a type it records no
    file for is nested in. ``resolved`` holds each tie made, by its case-folded
    name or type id."""

    owners: tuple[str, ...]
    records: tuple[NamedRecord, ...]
    nested: tuple[str, ...]
    resolved: dict[object, str | None] = field(default_factory=dict)


def _chosen(records: Iterable[NamedRecord]) -> str | None:
    """The file of records that give one thing: the one on this machine, else the
    first recorded. None when there are none."""
    found = list(records)
    return next((r.key for r in found if r.exists), found[0].key if found else None)


def tie_typedef(records: TypeRecords, type_id: int) -> str | None:
    """The file whose record's offset list names the typedef instance of type id
    ``type_id``."""
    return _chosen(r for r in records.records if type_id in r.type_ids)


def tie_class(records: TypeRecords, name: str) -> str | None:
    """The file whose record gives the class the qualified name ``name``."""
    return _chosen(r for r in records.records if r.is_named(name, records.owners))


def type_names(types: Iterable[LVType | None]) -> list[LVType]:
    """Every class and typedef type in ``types``, their elements, values, fields
    and connector pane slots included."""
    found: list[LVType] = []
    pending = [t for t in types if t is not None]
    seen: set[int] = set()
    while pending:
        lv_type = pending.pop()
        if id(lv_type) in seen:
            continue
        seen.add(id(lv_type))
        if lv_type.classname or lv_type.typedef_name:
            found.append(lv_type)
        for inner in (lv_type.element_type, lv_type.value_type):
            if inner is not None:
                pending.append(inner)
        pending.extend(f.type for f in lv_type.fields or () if f.type is not None)
        pending.extend(slot.type for slot in lv_type.pane or ())
    return found


def resolve_type(
    file_key: str,
    lv_type: LVType,
    records_of: Callable[[str], TypeRecords | None],
) -> str | None:
    """The file the class or typedef ``lv_type`` denotes in the VI or control
    ``file_key``: a typedef the file's records name by type id, else the file the
    record of its name gives, else the one the records of the dependencies it is
    nested in give it. None when the file stores no records or none gives it."""
    own = records_of(file_key)
    if own is None:
        return None
    if lv_type.typedef_name and not lv_type.classname and lv_type.type_id is not None:
        tied = own.resolved.get(lv_type.type_id)
        if tied is None:
            tied = tie_typedef(own, lv_type.type_id)
            own.resolved[lv_type.type_id] = tied
        if tied is not None:
            return tied
    name = lv_type.classname or lv_type.typedef_name
    return resolve_name(file_key, name, records_of) if name else None


def resolve_name(
    file_key: str,
    name: str,
    records_of: Callable[[str], TypeRecords | None],
) -> str | None:
    """The file the qualified name ``name`` denotes in ``file_key``: the one its
    own records give it, else the one its dependencies' records give it (nearest
    first, each dependency once)."""
    own = records_of(file_key)
    if own is None:
        return None
    folded = name.casefold()
    if folded in own.resolved:
        return own.resolved[folded]
    found = tie_class(own, name)
    seen = {file_key}
    level = list(own.nested)
    while found is None and level:
        next_level: list[str] = []
        matches: list[str] = []
        for key in level:
            if key in seen:
                continue
            seen.add(key)
            records = records_of(key)
            if records is None:
                continue
            tied = tie_class(records, name)
            if tied is not None:
                matches.append(tied)
            next_level.extend(records.nested)
        found = matches[0] if matches else None
        level = next_level
    own.resolved[folded] = found
    return found
