"""The flattened form of a Map and of a Set (``CollectionLayout``).

NI, "Flattened Data" (docs-be.ni.com/api/bundle/labview/page/flattened-data.html):
"A flattened set is an unsigned 32-bit integer that records the number of set
elements, followed by each element in the sort order of the set iterator, flattened
appropriately for the data type of the element." and "A flattened map is an unsigned
32-bit integer that records the number of map elements, followed by each key-value
pair flattened in key iterator order." A Map or Set iterates in sorted key order
(``collection_keys.py`` quotes NI), so a write sorts the keys; a read builds a
``dict`` or ``set``. The corpus agrees: the 37-byte Map ``{'PinGroup1': ['Pin1',
'Pin2']}`` is a count of 1, the key and the value (``docs/_internal/design/
labview-binary-format.md``).

A key that is a cluster or an array is a tuple of its values in generated code
(``collection_keys.py``), so it is written by position and read back as a tuple.
Only numbers, Booleans and strings, alone or inside clusters and arrays, have a
sort order here; another key type (a path, a timestamp, a refnum) is a gap.
"""

from __future__ import annotations

from dataclasses import dataclass

from lvkit.models import LVType

from .context import CodeGenContext
from .flat import (
    Field,
    Fixed,
    Integer,
    Layout,
    LineWriter,
    Part,
    Raw,
    Real,
    Record,
    Sequence,
    Text,
    UnsupportedLayout,
    layout_of,
    render_parts,
)


def _check_key(layout: Layout, what: str) -> None:
    """Raises ``UnsupportedLayout`` for a key with no sort order here."""
    if isinstance(layout, Record):
        for field in layout.fields:
            _check_key(field.layout, what)
    elif isinstance(layout, Sequence):
        _check_key(layout.element, what)
    elif isinstance(layout, Real) and len(layout.code) == 2:
        raise UnsupportedLayout(f"{what} of complex numbers, which have no order")
    elif not isinstance(layout, (Integer, Real, Text)):
        raise UnsupportedLayout(
            f"{what} of {layout.helper_label()}, whose sort order is not settled"
        )


def _by_position(layout: Layout) -> Layout:
    """``layout`` with every cluster read by position, as a key tuple is."""
    if isinstance(layout, Record):
        return Record(
            layout.label,
            False,
            tuple(Field(f.name, _by_position(f.layout)) for f in layout.fields),
        )
    if isinstance(layout, Sequence):
        return Sequence(_by_position(layout.element), layout.dimensions)
    return layout


def _as_key(layout: Layout, text: str, depth: int = 0) -> str:
    """The expression turning ``text``, a value read by ``layout``, into the key
    tuple the generated code uses."""
    if isinstance(layout, Record):
        parts = [
            _as_key(
                f.layout, f"{text}.{f.name}" if layout.named else f"{text}[{i}]", depth
            )
            for i, f in enumerate(layout.fields)
        ]
        return "(" + ", ".join(parts) + ("," if len(parts) == 1 else "") + ")"
    if isinstance(layout, Sequence):
        return _array_key(layout.element, text, layout.dimensions, depth)
    return text


def _array_key(element: Layout, text: str, dimensions: int, depth: int) -> str:
    name = f"_k{depth}"
    if dimensions > 1:
        inner = _array_key(element, name, dimensions - 1, depth + 1)
    else:
        inner = _as_key(element, name, depth + 1)
    return f"tuple({text})" if inner == name else f"tuple({inner} for {name} in {text})"


@dataclass(frozen=True)
class CollectionLayout(Layout):
    """A Set (``value`` None) or a Map of ``key`` to ``value``."""

    key: Layout
    value: Layout | None

    def fixed_size(self) -> int | None:
        return None

    def helper_label(self) -> str:
        return "set" if self.value is None else "map"

    def flatten(self, value, prefix, depth, fns) -> list[Part]:
        name = f"_c{depth}"
        key_layout = _by_position(self.key)
        item = render_parts(key_layout.flatten(name, True, depth + 1, fns), fns.order)
        if self.value is not None:
            member = render_parts(
                self.value.flatten(f"{value}[{name}]", True, depth + 1, fns), fns.order
            )
            item = f"{item} + {member}"
        return [
            Fixed("I", (f"len({value})",)),
            Raw(f"b''.join({item} for {name} in sorted({value}))"),
        ]

    def unflatten(self, target, prefix, out: LineWriter, fns) -> None:
        count = out.temporary("count")
        out.add(f"{count} = struct.unpack_from({fns.order + 'I'!r}, data, pos)[0]")
        out.add("pos += 4")
        out.add(f"{target} = set()" if self.value is None else f"{target} = {{}}")
        out.add(f"for _ in range({count}):")
        out.indent += 1
        key = out.temporary("key")
        self.key.unflatten(key, True, out, fns)
        as_key = _as_key(self.key, key)
        if self.value is None:
            out.add(f"{target}.add({as_key})")
        else:
            member = out.temporary("member")
            self.value.unflatten(member, True, out, fns)
            out.add(f"{target}[{as_key}] = {member}")
        out.indent -= 1


def collection_layout(lv_type: LVType, ctx: CodeGenContext | None) -> CollectionLayout:
    """The layout of the Set or Map ``lv_type``; ``UnsupportedLayout`` when a member
    type is unknown or the key has no sort order."""
    is_map = lv_type.underlying_type == "Map"
    what = "a Map" if is_map else "a Set"
    if lv_type.element_type is None or (is_map and lv_type.value_type is None):
        raise UnsupportedLayout(f"{what} whose member types are not known")
    key = layout_of(lv_type.element_type, ctx)
    _check_key(key, f"{what} with a key")
    value = (
        layout_of(lv_type.value_type, ctx) if is_map and lv_type.value_type else None
    )
    return CollectionLayout(key, value)
