"""``.ctl`` controls in the index: build each control's facts from the graph read
model (``InMemoryVIGraph.get_typedef``), refresh them incrementally on content
hash, and keep the store in step. The control counterpart of ``build.py``'s VI
build/refresh; both feed the same shared ``types`` catalog."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .. import cache_paths
from ..graph import load_ctl_by_path
from ..graph.node_kinds import NodeType
from ..graph.typedef import (
    TypedefInfo,
    default_literal,
    field_default_literal,
    walk_typedef_fields,
)
from .model import TypedefFacts, TypedefFieldFact, TypedefRefFact, TypedefRel
from .store import apply_typedefs, load_typedef_dependents, load_typedef_shas
from .types import TypeCatalog

logger = logging.getLogger(__name__)


def _facts_from_info(
    info: TypedefInfo, catalog: TypeCatalog, sha: str, path: str
) -> TypedefFacts:
    root_id = catalog.add(info.root_type)
    rows = [
        TypedefFieldFact(
            seq=w.seq,
            parent_seq=w.parent_seq,
            depth=w.depth,
            name=w.field.name,
            type_id=catalog.add(w.field.lv_type),
            default_text=field_default_literal(w.field),
        )
        for w in walk_typedef_fields(info.fields)
    ]
    refs = [
        TypedefRefFact(r.path, r.qualified, r.node_type.value, rel)
        for rel, group in (
            (TypedefRel.USES, info.uses),
            (TypedefRel.OWNED_BY, info.owned_by),
        )
        for r in group
    ]
    library = next(
        (r.qualified for r in info.owned_by if r.node_type is NodeType.LIBRARY), None
    )
    return TypedefFacts(
        path=path,
        name=Path(path).name,
        library=library,
        content_sha=sha,
        type_id=root_id,
        kind=info.root_type.kind,
        default_text=default_literal(info.default, info.root_type, info.elements),
        fields=rows,
        refs=refs,
        types=list(catalog.types.values()),
    )


def build_typedef_facts(
    project_root: Path, ctl_path: Path, sha: str | None = None
) -> TypedefFacts:
    """The facts of one control. A control that cannot be read yields a stub fact
    naming the reason (its content hash recorded, so it is retried only when the
    file changes). ``sha`` is the file's content hash when the caller has it."""
    resolved = ctl_path.resolve()
    sha = sha or cache_paths.sha256_file(resolved)
    try:
        graph, key = load_ctl_by_path(resolved, search_paths=[project_root])
        info = graph.get_typedef(key)
        catalog = TypeCatalog(lambda t: graph.get_type_fields(t, key))
        return _facts_from_info(info, catalog, sha, str(resolved))
    except Exception as e:  # noqa: BLE001 -- one unreadable control must not stop a sync
        logger.warning("index: %s could not be read as a control: %s", resolved, e)
        return TypedefFacts(
            path=str(resolved),
            name=resolved.name,
            is_stub=True,
            stub_reason=f"{type(e).__name__}: {e}",
            content_sha=sha,
        )


@dataclass
class TypedefSyncResult:
    """What a sync did: how many controls are now indexed, and the paths of the
    controls it rebuilt or dropped."""

    controls: int
    rebuilt: list[str]
    deleted: list[str]


def _with_dependents(
    seeds: set[str], current: dict[str, Path], dependents: dict[str, set[str]]
) -> set[str]:
    """``seeds`` (controls that changed or were deleted) plus every IN-CURRENT
    control that (transitively) uses one of them. A control's own fields and
    type_id can be resolved through a typedef reference it uses, so it must be
    rebuilt when that dependency changes even though its own bytes did not."""
    seen = set(seeds)
    frontier = set(seeds)
    while frontier:
        grown = {d for ref in frontier for d in dependents.get(ref, ())} - seen
        seen |= grown
        frontier = grown
    return {p for p in seen if p in current}


def _rebuild_facts(
    project_root: Path,
    to_rebuild: set[str],
    current: dict[str, Path],
    shas: dict[str, str],
) -> list[TypedefFacts]:
    return [
        build_typedef_facts(project_root, current[path], shas[path])
        for path in sorted(to_rebuild)
    ]


def sync_typedefs(
    project_root: Path, ctl_paths: list[Path], *, rebuild: bool = False
) -> TypedefSyncResult:
    """Make the stored control facts match the files: rebuild new / changed
    controls, every control that (transitively) uses one of them -- its own
    fields/type_id are read through that dependency (or every control, when
    ``rebuild``) -- drop the ones whose file is gone (always), keep the rest,
    and persist."""
    stored = load_typedef_shas(project_root)
    current = {str(p.resolve()): p for p in ctl_paths}
    shas = {path: cache_paths.sha256_file(p) for path, p in current.items()}
    deleted = [path for path in stored if path not in current]
    if rebuild:
        to_rebuild = set(current)
    else:
        dirty = {path for path in current if stored.get(path) != shas[path]}
        to_rebuild = _with_dependents(
            dirty | set(deleted), current, load_typedef_dependents(project_root)
        )
    changed = _rebuild_facts(project_root, to_rebuild, current, shas)
    apply_typedefs(project_root, changed, deleted)
    return TypedefSyncResult(
        controls=len(current), rebuilt=[t.path for t in changed], deleted=deleted
    )


def warm_typedefs(project_root: Path, ctl_paths: list[Path]) -> None:
    """Index the given controls that are new or changed, leaving every other stored
    control alone (a load that saw only some of the project's controls). Does not
    chase dependents -- a full :func:`sync_typedefs` catches those; this is a
    best-effort top-up from a load that already has the fresh graph in hand."""
    current = {str(p.resolve()): p for p in ctl_paths}
    stored = load_typedef_shas(project_root)
    shas = {path: cache_paths.sha256_file(p) for path, p in current.items()}
    changed_paths = {path for path in current if stored.get(path) != shas[path]}
    changed = _rebuild_facts(project_root, changed_paths, current, shas)
    if changed:
        apply_typedefs(project_root, changed, [])
