"""Describe a ``.ctl`` typedef as text or JSON -- the ``.ctl`` counterpart of
``describe_vi``. Types are shown as LabVIEW types (never a Python annotation)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .describe import default_suffix, type_label
from .load_ctl import load_ctl_by_path
from .netlist_json import typedef_to_dict
from .typedef import TypedefField, TypedefInfo, TypedefRef

_INDENT = "  "


def _default_text(f: TypedefField) -> str:
    """`` = <default>`` for a field's recorded default, or its saved elements as
    ``= [a, b, ...]``. A string is quoted; a number or an enum item is not (its
    recorded default is display text, but it is not a string value)."""
    if f.elements:
        return " = [" + ", ".join(str(e) for e in f.elements) + "]"
    if f.default is None:
        return ""
    is_string = f.lv_type is not None and f.lv_type.underlying_type == "String"
    return default_suffix(f.default) if is_string else f" = {f.default}"


def _field_lines(fields: tuple[TypedefField, ...], depth: int) -> list[str]:
    lines: list[str] = []
    for f in fields:
        pad = _INDENT * depth
        lines.append(f"{pad}{f.name}: {type_label(f.lv_type)}{_default_text(f)}")
        lines.extend(_field_lines(f.fields, depth + 1))
    return lines


def _ref_line(ref: TypedefRef, verbose: bool) -> str:
    line = f"{_INDENT}{ref.qualified} ({ref.node_type.value})"
    if verbose and ref.path is not None:
        line += f" -- {ref.path}"
    return line


def describe_typedef(info: TypedefInfo, *, verbose: bool = False) -> str:
    """One typedef as a documentation page: its type, then ``## Values`` (an
    enum / ring's items), ``## Fields`` (a cluster's, with their defaults),
    ``## Uses``, ``## Owned by`` and ``## Used by``. A section with nothing in it
    is left out, so an absent section never reads as "nothing uses this" for a
    control loaded alone. ``verbose`` adds file paths."""
    root = info.root_type
    lines = [f"# {info.name}", ""]
    if verbose:
        lines.append(f"{_INDENT}Path: {info.key}")
    if info.fields:
        lines.append(f"{_INDENT}Cluster ({len(info.fields)} fields)")
    else:
        lines.append(f"{_INDENT}Type: {type_label(root)}")
    if info.default is not None:
        lines.append(f"{_INDENT}Default{default_suffix(info.default)}")
    lines.append("")
    if root.values:
        lines.append("## Values")
        for name, item in sorted(root.values.items(), key=lambda kv: kv[1].value):
            lines.append(f"{_INDENT}{item.value}: {name}")
        lines.append("")
    if info.fields:
        lines.append("## Fields")
        lines.extend(_field_lines(info.fields, 1))
        lines.append("")
    for title, refs in (
        ("Uses", info.uses),
        ("Owned by", info.owned_by),
        ("Used by", info.used_by),
    ):
        if refs:
            lines.append(f"## {title}")
            lines.extend(_ref_line(r, verbose) for r in refs)
            lines.append("")
    return "\n".join(lines).rstrip("\n")


def describe_ctl_file(
    path: Path,
    *,
    fmt: str = "text",
    verbose: bool = False,
    search_paths: list[Path] | None = None,
) -> str | dict[str, Any]:
    """Load the ``.ctl`` at ``path`` and describe it: the text page for
    ``fmt="text"``, the JSON shape (:func:`typedef_to_dict`) for ``"json"``. The
    one entry the CLI and the MCP ``read_ctl`` tool share. ``ValueError`` for
    ``lvnet`` (a dataflow netlist -- a control has no dataflow) or an unreadable
    control; ``FileNotFoundError`` for a missing file."""
    if fmt == "lvnet":
        raise ValueError("--format lvnet describes a VI's dataflow; a .ctl has none")
    graph, key = load_ctl_by_path(path, search_paths=search_paths)
    info = graph.get_typedef(key)
    if fmt == "json":
        return typedef_to_dict(info, verbose=verbose)
    return describe_typedef(info, verbose=verbose)
