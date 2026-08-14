"""Batch-collect every dependency-resolution gap in a VI / library / project.

The one-at-a-time conversion loop (`lvkit generate`) stops at the FIRST unknown
primitive or unmapped vi.lib VI it hits, raising `PrimitiveResolutionNeeded` /
`VILibResolutionNeeded` so the gap can be filled before proceeding. That's the
right shape for producing a complete port, but a poor shape for triage: you fix
one gap, re-run, hit the next, repeat.

`collect_unresolved` walks the whole conversion order once in soft-collect mode
(``build_module(..., unresolved_sink=...)``) so every gap is recorded in a
single pass, then aggregates them by identity. It reuses the exact diagnostics
the exceptions carry — no parallel resolution logic.

Scope: unknown primitives (no entry in primitives.json) and unmapped vi.lib VIs
(no entry in data/vilib/). A resolved VI with an unmappable *terminal* index
(`TerminalResolutionNeeded`) is a different class of gap — usually a
search-path problem, not a missing mapping — and is reported per-VI as a
``terminal_mapping`` item (first occurrence), not exhaustively.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .codegen.builder import build_module
from .graph import InMemoryVIGraph
from .graph.loading import LoadMode
from .primitive_resolver import PrimitiveResolutionNeeded, TerminalResolutionNeeded
from .vilib_resolver import VILibResolutionNeeded
from .vilib_resolver import get_resolver as get_vilib_resolver


@dataclass
class UnresolvedItem:
    """One distinct resolution gap, aggregated across every VI it appears in."""

    kind: str  # "unknown_primitive" | "unmapped_vilib" | "terminal_mapping"
    identifier: str  # primResID for a primitive; VI filename otherwise
    name: str  # human-readable name
    vi_names: list[str] = field(default_factory=list)  # VIs where it occurs
    count: int = 0  # total occurrences across all VIs
    detail: str = ""  # the full formatted diagnostic (from first occurrence)


def _load_graph(
    input_path: Path,
    mode: LoadMode,
    search_paths: list[Path] | None,
    vilib_root: Path | None,
    userlib_root: Path | None,
) -> InMemoryVIGraph:
    """Build the graph for a .vi / .lvclass / .lvlib / .llb / directory input."""
    graph = InMemoryVIGraph()
    if vilib_root or userlib_root:
        graph.set_library_roots(vilib_root=vilib_root, userlib_root=userlib_root)
    sp: list[Path] = list(search_paths) if search_paths else []
    suffix = input_path.suffix.lower()
    if suffix == ".lvclass":
        graph.load_lvclass(str(input_path), mode, search_paths=sp)
    elif suffix == ".lvlib":
        graph.load_lvlib(str(input_path), mode, search_paths=sp)
    elif suffix == ".llb":
        graph.load_llb(str(input_path), mode, search_paths=sp)
    elif input_path.is_dir():
        graph.load_directory(str(input_path), mode, search_paths=sp)
    else:
        graph.load_vi(str(input_path), mode, search_paths=sp)
    return graph


def _item_key(exc: Exception) -> tuple[str, str, str]:
    """(kind, identifier, name) for an appended resolution exception."""
    if isinstance(exc, PrimitiveResolutionNeeded):
        # A placeholder is a KNOWN primitive with no implementation (tagged at
        # the emit site); a fully-unknown primResID is not in primitives.json.
        kind = (
            "placeholder_primitive"
            if getattr(exc, "is_placeholder", False)
            else "unknown_primitive"
        )
        return (kind, str(exc.prim_id), exc.prim_name or "unknown")
    if isinstance(exc, VILibResolutionNeeded):
        return ("unmapped_vilib", exc.vi_name, exc.vi_name)
    if isinstance(exc, TerminalResolutionNeeded):
        return ("terminal_mapping", exc.prim_name, exc.prim_name)
    return ("unknown", str(exc), str(exc))


def _vi_of(exc: Exception) -> str | None:
    """The qualified VI name an exception was raised in, if it carries one."""
    return getattr(exc, "qualified_vi_name", None) or getattr(exc, "vi_name", None)


def collect_unresolved(
    input_path: Path | str,
    search_paths: list[Path] | None = None,
    mode: LoadMode = LoadMode.FULL,
    vilib_root: Path | None = None,
    userlib_root: Path | None = None,
) -> list[UnresolvedItem]:
    """Return every distinct resolution gap under ``input_path``, aggregated.

    Sorted most-frequent first, then by kind and identifier for stable output.
    """
    input_path = Path(input_path)
    graph = _load_graph(input_path, mode, search_paths, vilib_root, userlib_root)
    vilib_resolver = get_vilib_resolver()

    sink: list[Exception] = []
    for vi_name in graph.get_conversion_order():
        # Skip VIs we don't generate from their own graph: stubs, and vi.lib
        # VIs that already have a provided implementation (a resolved dep).
        if graph.is_stub_vi(vi_name) or vilib_resolver.has_implementation(vi_name):
            continue
        try:
            vi_context = graph.get_vi_context(vi_name)
            build_module(
                vi_context, vi_name, graph=graph,
                soft_unresolved=True, unresolved_sink=sink,
            )
        except (
            PrimitiveResolutionNeeded,
            VILibResolutionNeeded,
            TerminalResolutionNeeded,
        ) as e:
            # Softened sites append to the sink and continue; only the
            # not-yet-softened terminal-resolution sites still raise. Record
            # that first gap and move to the next VI.
            sink.append(e)
        except Exception:  # noqa: BLE001 — per-VI; a build failure isn't a gap
            continue

    # Aggregate by identity.
    items: dict[tuple[str, str, str], UnresolvedItem] = {}
    for exc in sink:
        key = _item_key(exc)
        item = items.get(key)
        if item is None:
            item = UnresolvedItem(
                kind=key[0], identifier=key[1], name=key[2],
                detail=str(exc),
            )
            items[key] = item
        item.count += 1
        vi = _vi_of(exc)
        if vi and vi not in item.vi_names:
            item.vi_names.append(vi)

    return sorted(
        items.values(),
        key=lambda it: (-it.count, it.kind, it.identifier),
    )


def format_unresolved_report(items: list[UnresolvedItem], target: str) -> str:
    """Human-readable report of collected gaps (used by the CLI)."""
    if not items:
        return f"No unresolved primitives or vi.lib VIs in {target}."

    _labels = {
        "unknown_primitive": "Unknown primitives (not in primitives.json)",
        "placeholder_primitive": "Placeholder primitives (known, no implementation)",
        "unmapped_vilib": "Unmapped vi.lib VIs",
        "terminal_mapping": "Terminal-mapping gaps",
    }
    _order = (
        "unknown_primitive",
        "placeholder_primitive",
        "unmapped_vilib",
        "terminal_mapping",
    )

    def _n(kind: str) -> int:
        return sum(1 for it in items if it.kind == kind)

    lines = [
        f"# Unresolved dependencies in {target}",
        "",
        f"{len(items)} distinct gap(s): "
        f"{_n('unknown_primitive')} unknown primitive(s), "
        f"{_n('placeholder_primitive')} placeholder primitive(s), "
        f"{_n('unmapped_vilib')} unmapped vi.lib VI(s), "
        f"{_n('terminal_mapping')} terminal-mapping gap(s).",
    ]
    for kind in _order:
        group = [it for it in items if it.kind == kind]
        if not group:
            continue
        lines += ["", f"## {_labels[kind]}"]
        for it in group:
            head = (
                f"[prim {it.identifier}] {it.name}"
                if kind in ("unknown_primitive", "placeholder_primitive")
                else it.name
            )
            where = f" — {it.count}x in {len(it.vi_names)} VI(s)"
            lines.append(f"- {head}{where}")
            for vi in it.vi_names[:8]:
                lines.append(f"    · {vi}")
            if len(it.vi_names) > 8:
                lines.append(f"    · … and {len(it.vi_names) - 8} more")
    return "\n".join(lines)
