"""Audit of stored-value decoding: every front-panel default and diagram constant
of one VI, decoded by ``_decode_element`` and compared with its stored size.

A value is *exact* when the decoder consumed every stored byte AND read every
field and element its type declares. The second condition matters because a
reader that gives the rest of the data to one field (or stops early) can still
land on the stored size while leaving later fields unread, so each problem seen
anywhere inside a value is recorded, not only a size difference.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lvkit.measure_data import TIMESTAMP_FLAVOR
from lvkit.models import LVType, LVTypeKind, UndecodedPicture

from . import vi as _vi
from .type_mapping import parse_type_map_rich
from .type_resolution import resolve_type_rich
from .vi import MAX_STORED_ARRAY_ELEMENTS


@dataclass
class ValueAudit:
    """One stored value and how far the decoder got through it."""

    kind: str  # "fp" front-panel default, "bd" diagram constant
    size: int
    consumed: int
    decoded: bool
    type: str
    problems: list[str] = field(default_factory=list)
    # Parts read to their exact extent but whose contents are not decoded (a class
    # object's private data): reported apart from ``problems``.
    undecoded: list[str] = field(default_factory=list)
    tail: str = ""
    raw: str = ""  # the stored bytes (first 256), kept only for a value not exact

    @property
    def exact(self) -> bool:
        return self.decoded and self.size == self.consumed and not self.problems

    @property
    def cause(self) -> str:
        """The first problem seen, else the size difference."""
        if self.problems:
            return self.problems[0]
        if not self.decoded:
            return "no value"
        if self.consumed < self.size:
            return "unread tail"
        return "over-read"


def _type_key(lv_type: LVType) -> str:
    """A short type label for grouping: what the type is, never a name."""
    parts = [lv_type.underlying_type or lv_type.kind.value]
    if lv_type.ref_type:
        parts.append(lv_type.ref_type)
    if lv_type.typedef_name:
        parts.append("typedef")
    return "/".join(parts)


class _Trace:
    """Wraps ``_decode_element`` to record problems at every depth."""

    def __init__(self) -> None:
        self.depth = 0
        self.problems: list[str] = []
        self.undecoded: list[str] = []
        self.top: list[tuple[bytes, int, bool, LVType, list[str], list[str]]] = []
        self._original = _vi._decode_element

    def __call__(self, data, elem_type):  # type: ignore[no-untyped-def]
        top = self.depth == 0
        if top:
            self.problems = []
            self.undecoded = []
        self.depth += 1
        try:
            value, consumed, structured = self._original(data, elem_type)
        finally:
            self.depth -= 1
        if elem_type is not None and len(data) > 0:
            self._inspect(data, elem_type, value, consumed, structured)
        if top and elem_type is not None:
            self.top.append(
                (
                    bytes(data),
                    consumed,
                    value is not None or consumed > 0,
                    elem_type,
                    list(self.problems),
                    list(self.undecoded),
                )
            )
        return value, consumed, structured

    def _inspect(self, data, lv_type, value, consumed, structured) -> None:  # type: ignore[no-untyped-def]
        underlying = lv_type.underlying_type or ""
        key = _type_key(lv_type)
        if value is None and consumed == 0:
            self.problems.append(f"unreadable {key}")
        elif (
            underlying == "MeasureData"
            and lv_type.measure_flavor == TIMESTAMP_FLAVOR
            and consumed != 16
        ):
            self.problems.append(f"short {key}")
        elif isinstance(structured, UndecodedPicture):
            self.undecoded.append("picture drawing")
        elif underlying == "Refnum" and lv_type.classname and consumed:
            extent = _vi.read_class_object(data)
            if extent is not None and any(s != e for s, e in extent.private):
                self.undecoded.append("class private data")
        elif lv_type.kind == LVTypeKind.CLUSTER and lv_type.fields:
            if isinstance(structured, dict) and len(structured) < len(lv_type.fields):
                missing = lv_type.fields[len(structured)].type
                self.problems.append(f"cluster stops before {_type_key(missing)}")
        elif lv_type.kind == LVTypeKind.ARRAY and lv_type.element_type:
            dims = lv_type.dimensions or 1
            if len(data) >= 4 * dims and isinstance(structured, list):
                sizes = [
                    int.from_bytes(data[4 * d : 4 * d + 4], "big") for d in range(dims)
                ]
                array_len = math.prod(sizes)
                if (
                    array_len <= MAX_STORED_ARRAY_ELEMENTS
                    and _leaf_count(structured) < array_len
                ):
                    self.problems.append(
                        f"array stops before {_type_key(lv_type.element_type)}"
                    )


def _leaf_count(value: object) -> int:
    if isinstance(value, list):
        return sum(_leaf_count(v) for v in value)
    return 1


def constant_padding(data: bytes, consumed: int) -> int:
    """The extra byte a diagram constant stores after a value that is all zero
    bytes: 1 when the ``consumed`` value bytes are all zero and exactly one zero
    byte follows them, else 0."""
    if not any(data[:consumed]) and data[consumed:] == b"\x00":
        return 1
    return 0


def audit_vi(
    main_xml: str | Path, bd_xml: str | Path | None, fp_xml: str | Path
) -> list[dict]:
    """Audit every stored value of one VI/control (from its extracted XML)."""
    trace = _Trace()
    _vi._decode_element = trace  # type: ignore[assignment]
    try:
        parsed = _vi.parse_vi(main_xml=main_xml, bd_xml=bd_xml, fp_xml=fp_xml)
        kinds = ["fp"] * len(trace.top)
        type_map = parse_type_map_rich(main_xml)
        for const in parsed.block_diagram.constants:
            lv_type = (
                resolve_type_rich(const.type_desc, type_map)
                if const.type_desc
                else None
            )
            if lv_type is None or const.value is None:
                continue
            kinds.append("bd")
            trace(bytes.fromhex(const.value), lv_type)
    finally:
        _vi._decode_element = trace._original  # type: ignore[assignment]
    records = []
    for kind, (data, consumed, decoded, lv_type, problems, undecoded) in zip(
        kinds, trace.top
    ):
        audit = ValueAudit(
            kind=kind,
            size=len(data),
            consumed=consumed,
            decoded=decoded,
            type=lv_type.type_descriptor(),
            problems=problems,
            undecoded=undecoded,
        )
        if kind == "bd":
            audit.consumed += constant_padding(data, consumed)
            if consumed and len(data) == consumed and not any(data):
                audit.problems.append("zero constant without its extra byte")
        if not audit.exact:
            audit.tail = data[consumed : consumed + 24].hex()
            audit.raw = data[:256].hex()
        record = asdict(audit)
        record["exact"] = audit.exact
        record["cause"] = audit.cause
        records.append(record)
    return records
