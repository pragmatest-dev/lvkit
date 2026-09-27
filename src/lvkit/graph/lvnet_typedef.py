"""lvnet text for a ``.ctl`` typedef: a ``typedef <Name> :`` document, the
``.ctl`` counterpart of ``render_lvnet``'s ``vi <Name> :`` (see
``docs/_internal/design/netlist-language.md`` §2.1).

Sections, each omitted when empty: ``uses :`` (the ``<kind> <qualified> ; ./path``
manifest ``render_lvnet_uses`` writes for a VI), ``type :`` (the control's root
type in the lossless §10.1 grammar), ``default`` (a scalar / enum control's own
default), ``fields :`` (a cluster's fields, each ``<name> : <type-ref>`` with its
recorded ``default <value>``; a nested cluster / array-of-cluster indents its own
fields under it), and -- verbose only -- ``types :``, the lossless definition of
every NAMED type the control's type reaches.

Emit only: there is no parser for this form.
"""

from __future__ import annotations

from pathlib import Path

from ..models import LVType
from .lvnet_grammar import (
    _FIELDS_HEADER_LINE,
    _LVNET_BLOCK_OPEN,
    _LVNET_DEFAULT_KEYWORD,
    _LVNET_INDENT,
    _LVNET_TYPE_SEP,
    _TYPE_KEYWORD,
    _TYPEDEF_KEYWORD,
)
from .netlist_models import DependencyKind, NetlistDependency
from .render_lvnet import (
    iter_named_subtypes,
    lvnet_literal_token,
    lvnet_name_token,
    lvnet_named_stem,
    lvnet_type_lossless_def,
    lvnet_type_ref,
    project_relative_display,
    render_lvnet_named_types,
    render_lvnet_uses,
)
from .typedef import (
    TypedefField,
    TypedefInfo,
    TypedefRef,
    default_literal,
    field_default_literal,
    walk_typedef_fields,
)


def _dependency(ref: TypedefRef, control_dir: Path) -> NetlistDependency:
    """``ref`` as a ``uses :`` entry, its path relative to the control's own
    directory; no path for a stub, which has no file to point at."""
    path = None if ref.path is None else project_relative_display(
        Path(ref.path), control_dir
    )
    return NetlistDependency(
        DependencyKind.for_node_type(ref.node_type), ref.qualified, path
    )


def _field_lines(fields: tuple[TypedefField, ...], depth: int) -> list[str]:
    lines: list[str] = []
    for w in walk_typedef_fields(fields, depth):
        f = w.field
        name = lvnet_name_token(f.name)
        line = f"{_LVNET_INDENT * w.depth}{name}{_LVNET_TYPE_SEP}"
        line += lvnet_type_ref(f.lv_type)
        literal = field_default_literal(f, lvnet_literal_token, lvnet_name_token)
        if literal is not None:
            line += f" {_LVNET_DEFAULT_KEYWORD} {literal}"
        lines.append(line)
    return lines


def _named_types(root: LVType) -> dict[str, LVType]:
    """Every NAMED type the control's type reaches, by name, excluding the control
    itself (its definition is the ``type :`` line)."""
    own = lvnet_named_stem(root)
    seen: dict[str, LVType] = {}
    for name, lv_type in iter_named_subtypes(root):
        if name != own:
            seen.setdefault(name, lv_type)
    return dict(sorted(seen.items()))


def render_lvnet_typedef(info: TypedefInfo, *, verbose: bool = False) -> str:
    """The lvnet ``typedef`` document for ``info``. ``verbose`` adds the ``types :``
    footnote of the named types its type reaches."""
    lines = [f"{_TYPEDEF_KEYWORD} {info.name}{_LVNET_BLOCK_OPEN}"]
    try:
        uses = [_dependency(r, Path(info.key).parent) for r in info.uses]
    except ValueError as e:
        raise ValueError(f"{info.name}: {e}") from e
    render_lvnet_uses(uses, lines, verbose=verbose)
    lines.append(
        f"{_LVNET_INDENT}{_TYPE_KEYWORD}{_LVNET_TYPE_SEP}"
        f"{lvnet_type_lossless_def(info.root_type)}"
    )
    root_default = default_literal(
        info.default,
        info.root_type,
        info.elements,
        quote=lvnet_literal_token,
        word=lvnet_name_token,
    )
    if root_default is not None:
        lines.append(f"{_LVNET_INDENT}{_LVNET_DEFAULT_KEYWORD} {root_default}")
    if info.fields:
        lines.append(_FIELDS_HEADER_LINE)
        lines.extend(_field_lines(info.fields, 2))
    if verbose:
        render_lvnet_named_types(_named_types(info.root_type), lines)
    return "\n".join(lines)
