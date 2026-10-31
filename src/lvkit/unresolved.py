"""List every gap in a VI / library / project.

A gap is anything code generation cannot convert (see ``codegen/gaps.py``).
``collect_unresolved`` runs ``generate_python`` into a scratch folder it deletes
and aggregates the gaps that run found, so the list is exactly the warnings
``generate`` prints for the same input.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path

from lvkit.library_folders import Folders, SearchEntries

from .cache_paths import project_root
from .codegen.gaps import (
    MISSING_VI,
    PLACEHOLDER_PRIMITIVE,
    TERMINAL_MAPPING,
    UNKNOWN_DEFAULT,
    UNKNOWN_PRIMITIVE,
    UNMAPPED_VILIB,
    UNSUPPORTED_NODE,
    UNSUPPORTED_VI,
    Gap,
)
from .graph.loading import LoadMode
from .pipeline import generate_python
from .run_report import RunProblems, RunSettings
from .stderr_capture import capture_stdout


@dataclass
class UnresolvedItem:
    """One distinct resolution gap, aggregated across every VI it appears in."""

    # One of the kinds in ``codegen/gaps.py``.
    kind: str
    # primResID for a primitive; the node kind for an unsupported node; the VI
    # name otherwise
    identifier: str
    name: str  # human-readable name
    vi_names: list[str] = field(default_factory=list)  # VIs where it occurs
    count: int = 0  # total occurrences across all VIs
    detail: str = ""  # the full formatted diagnostic (from first occurrence)


def _item_key(gap: Gap) -> tuple[str, str, str]:
    """(kind, identifier, name) of a gap: what makes two gaps the same item."""
    return (gap.kind, gap.identifier, gap.name)


def collect_unresolved(
    input_path: Path | str,
    mode: LoadMode = LoadMode.FULL,
    vilib_root: Folders = None,
    userlib_root: Folders = None,
    instrlib_root: Folders = None,
    search_path: SearchEntries = None,
    run: RunProblems | None = None,
) -> list[UnresolvedItem]:
    """Return every distinct gap under ``input_path``, aggregated.

    Sorted most-frequent first, then by kind and identifier for stable output.
    Every other problem the run found (a VI that cannot be read, a VI that cannot
    be written) is recorded in ``run``; the gaps are the result, not problems.
    """
    input_path = Path(input_path)
    root = project_root(input_path)
    # Its own run: the gaps it finds are the result, so they are not problems; every
    # other problem prints and stops (``--fail-fast``) as this run's settings say.
    inner = RunProblems(
        replace(
            run.settings if run is not None else RunSettings(),
            root=root,
            gaps_are_problems=False,
        )
    )
    with tempfile.TemporaryDirectory(prefix="lvkit-gaps-") as scratch:
        with capture_stdout():
            result = generate_python(
                input_path,
                scratch,
                mode=mode,
                vilib_root=vilib_root,
                userlib_root=userlib_root,
                instrlib_root=instrlib_root,
                search_path=search_path,
                run=inner,
            )
    if run is not None:
        run.take(inner.report().problems, announce=False)

    items: dict[tuple[str, str, str], UnresolvedItem] = {}
    for gap in result["gaps"]:
        key = _item_key(gap)
        item = items.get(key)
        if item is None:
            item = UnresolvedItem(
                kind=gap.kind,
                identifier=gap.identifier,
                name=gap.name,
                detail=gap.detail,
            )
            items[key] = item
        item.count += 1
        try:
            vi = Path(gap.vi).relative_to(root).as_posix()
        except ValueError:
            vi = gap.vi
        if vi and vi not in item.vi_names:
            item.vi_names.append(vi)

    return sorted(
        items.values(),
        key=lambda it: (-it.count, it.kind, it.identifier),
    )


def format_unresolved_report(items: list[UnresolvedItem], target: str) -> str:
    """Human-readable report of collected gaps (used by the CLI)."""
    if not items:
        return f"No gaps in {target}."

    _labels = {
        UNKNOWN_PRIMITIVE: "Unknown primitives (not in primitives.json)",
        PLACEHOLDER_PRIMITIVE: "Placeholder primitives (known, no implementation)",
        UNSUPPORTED_NODE: "Nodes that cannot be converted",
        UNMAPPED_VILIB: "Unmapped vi.lib VIs",
        TERMINAL_MAPPING: "Terminal-mapping gaps",
        UNKNOWN_DEFAULT: "Unwired inputs whose default is not known",
        MISSING_VI: "VIs that were not found",
        UNSUPPORTED_VI: "VIs and modules that cannot be converted",
    }
    _order = tuple(_labels)

    def _n(kind: str) -> int:
        return sum(1 for it in items if it.kind == kind)

    lines = [
        f"# Gaps in {target}",
        "",
        f"{len(items)} distinct gap(s): "
        f"{_n(UNKNOWN_PRIMITIVE)} unknown primitive(s), "
        f"{_n(PLACEHOLDER_PRIMITIVE)} placeholder primitive(s), "
        f"{_n(UNSUPPORTED_NODE)} node(s) that cannot be converted, "
        f"{_n(UNMAPPED_VILIB)} unmapped vi.lib VI(s), "
        f"{_n(TERMINAL_MAPPING)} terminal-mapping gap(s), "
        f"{_n(UNKNOWN_DEFAULT)} input(s) whose default is not known, "
        f"{_n(MISSING_VI)} VI(s) not found, "
        f"{_n(UNSUPPORTED_VI)} VI(s) or module(s) that cannot be converted.",
    ]
    for kind in _order:
        group = [it for it in items if it.kind == kind]
        if not group:
            continue
        lines += ["", f"## {_labels[kind]}"]
        for it in group:
            if kind in (UNKNOWN_PRIMITIVE, PLACEHOLDER_PRIMITIVE):
                head = f"[prim {it.identifier}] {it.name}"
            elif kind == UNSUPPORTED_NODE:
                head = f"{it.identifier} {it.name}"
            elif kind == UNKNOWN_DEFAULT:
                head = f"{it.identifier}, input '{it.name}'"
            else:
                head = it.name
            where = f" — {it.count}x in {len(it.vi_names)} VI(s)"
            lines.append(f"- {head}{where}")
            for vi in it.vi_names[:8]:
                lines.append(f"    · {vi}")
            if len(it.vi_names) > 8:
                lines.append(f"    · … and {len(it.vi_names) - 8} more")
            if it.detail and it.detail != it.name:
                lines.extend(f"    | {line}" for line in it.detail.splitlines())
    return "\n".join(lines)
