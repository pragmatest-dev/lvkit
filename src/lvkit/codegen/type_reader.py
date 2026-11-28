"""The reader of a variant's type string that a generated package's ``variants``
module holds, so LabVIEW's type-introspection VIs (Get Array / Cluster / Numeric
Information) work on a type known only when the code runs.

The reader is the inverse of ``type_string.encode``: it reads the descriptor
layout NI's "Type Descriptors" page gives (``[size][type code][type specific
info][name]``, ``type_string.py``) with the standard library's ``struct``, and
returns a ``TypeInfo``. The tables it reads by (the codes of the numbers and of the
enums, the size word of a variable-size dimension, the constant of each scalar
type) are written into the module from the ones the encoder uses, so the two
cannot differ. A type with no type string is read from its code alone, which names
its kind. It reads no flattened data.
"""

from __future__ import annotations

from lvkit.lv_type_codes import MEASURE_DATA_FLAVOR, REFNUM_TYPE, TD_FULL_TYPE
from lvkit.type_string import ENUM_CODES, NUMBER_CODES, VARIABLE_SIZE

_READER_TEMPLATE = '''

# The reader of a variant's type string (the inverse of the encoder that wrote it).
# The name of each number and enum type, by code.
_NUMBER_NAMES = $NUMBER_NAMES
_ENUM_CODES = $ENUM_CODES
_BOOLEAN_CODE = $BOOLEAN
_STRING_CODE = $STRING
_ARRAY_CODE = $ARRAY
_CLUSTER_CODE = $CLUSTER
_REFNUM_CODE = $REFNUM
_VARIANT_CODE = $VARIANT
_CLASS_REFNUM_KIND = $CLASS_REFNUM
_MEASURE_CODE = $MEASURE
_TIMESTAMP_FLAVOR = $TIMESTAMP_FLAVOR
_VARIABLE_SIZE = $VARIABLE_SIZE
_SCALARS = {$SCALARS}


@dataclass(frozen=True)
class TypeInfo:
    """What a type records: its ``kind`` (``number``, ``enum``, ``boolean``,
    ``string``, ``array``, ``cluster``, ``refnum``, ``timestamp``, ``void`` or
    ``other``), the
    ``type`` itself, the name of a number (``NumInt32``), the ``name`` the
    descriptor records, an array's ``dimensions`` (``(variable, size)`` each), its
    ``element`` type, a cluster's member ``fields``, an enum's ``items`` and a
    refnum's ``refnum`` kind code."""

    kind: str
    type: LVType | None
    number: str | None = None
    name: str | None = None
    dimensions: tuple[tuple[bool, int], ...] = ()
    element: LVType | None = None
    fields: tuple[LVType, ...] = ()
    items: tuple[str, ...] = ()
    refnum: int | None = None


def _kind_of(code: int) -> str:
    if code in _ENUM_CODES:
        return "enum"
    if code in _NUMBER_NAMES:
        return "number"
    if code == _BOOLEAN_CODE:
        return "boolean"
    if code == _STRING_CODE:
        return "string"
    if code == _ARRAY_CODE:
        return "array"
    if code == _CLUSTER_CODE:
        return "cluster"
    if code == _REFNUM_CODE:
        return "refnum"
    return "void" if code == 0 else "other"


def _signed(word: int) -> int:
    return word - 0x10000 if word & 0x8000 else word


def _pascal(data: bytes, pos: int) -> tuple[str, int]:
    length = data[pos]
    if pos + 1 + length > len(data):
        raise ValueError("a name runs past the end of its descriptor")
    return data[pos + 1 : pos + 1 + length].decode("latin-1"), pos + 1 + length


def _trailing_name(words: list[int], start: int, end: int) -> str | None:
    """The Pascal name a descriptor appends after its type-specific words
    (``words[start:end]``), or None."""
    data = struct.pack(f">{end - start}H", *words[start:end])
    if not data or data[0] == 0:
        return None
    return _pascal(data, 0)[0]


def _member(words: list[int], pos: int) -> tuple[LVType, int]:
    """The type of the descriptor at word ``pos`` and the word after it."""
    if pos >= len(words):
        raise ValueError("the type string ends inside a descriptor")
    size = words[pos]
    length = size // 2
    if size < 4 or size % 2 or pos + length > len(words):
        raise ValueError(f"a descriptor has a size of {size} bytes it cannot hold")
    own = tuple(_signed(w) for w in words[pos : pos + length])
    code = words[pos + 1] & 0xFF
    scalar = _SCALARS.get(code)
    if scalar is not None and scalar.type_string == own:
        return scalar, pos + length
    info = _read_words(words[pos : pos + length], code)
    return LVType(code, info.name or _label(info), own), pos + length


def _label(info: TypeInfo) -> str:
    if info.kind == "array" and info.element is not None:
        return "[" * len(info.dimensions) + info.element.name + "]" * len(
            info.dimensions
        )
    return {
        "cluster": "cluster",
        "enum": "enum",
        "timestamp": "MeasureData",
    }.get(info.kind, info.kind)


def _read_words(words: list[int], code: int) -> TypeInfo:
    """The ``TypeInfo`` of the descriptor held by ``words`` (its size word first)."""
    kind = _kind_of(code)
    body = 2
    if code == _MEASURE_CODE and len(words) > body and (
        words[body] == _TIMESTAMP_FLAVOR
    ):
        # The subtype word, the inline cluster of four integers (its own size
        # word first, in bytes) and the name.
        if len(words) <= body + 1 or body + 1 + words[body + 1] // 2 > len(words):
            raise ValueError("a time stamp descriptor ends inside its cluster")
        return TypeInfo(
            "timestamp",
            None,
            name=_trailing_name(words, body + 1 + words[body + 1] // 2, len(words)),
        )
    if kind in ("number", "boolean"):
        return TypeInfo(
            kind,
            None,
            number=_NUMBER_NAMES.get(code),
            name=_trailing_name(words, body, len(words)),
        )
    if kind == "string":
        return TypeInfo(
            kind, None, name=_trailing_name(words, body + 2, len(words))
        )
    if kind == "array":
        count = words[body]
        pos = body + 1
        dimensions = []
        for _ in range(count):
            size = (words[pos] << 16) | words[pos + 1]
            pos += 2
            variable = size == (_VARIABLE_SIZE << 16 | _VARIABLE_SIZE)
            if not variable and size >> 24:
                raise ValueError(
                    f"an array dimension has the flags {size >> 24:#x}, which "
                    "this reader does not know"
                )
            dimensions.append((variable, 0 if variable else size))
        element, pos = _member(words, pos)
        return TypeInfo(
            kind,
            None,
            dimensions=tuple(dimensions),
            element=element,
            name=_trailing_name(words, pos, len(words)),
        )
    if kind == "cluster":
        count = words[body]
        pos = body + 1
        fields = []
        for _ in range(count):
            member, pos = _member(words, pos)
            fields.append(member)
        return TypeInfo(
            kind,
            None,
            fields=tuple(fields),
            name=_trailing_name(words, pos, len(words)),
        )
    if kind == "enum":
        count = words[body]
        data = struct.pack(f">{len(words) - body - 1}H", *words[body + 1 :])
        items = []
        pos = 0
        for _ in range(count):
            item, pos = _pascal(data, pos)
            items.append(item)
        pos += pos % 2
        name = _pascal(data, pos)[0] if pos < len(data) and data[pos] else None
        return TypeInfo(kind, None, number=_NUMBER_NAMES.get(code), name=name,
                        items=tuple(items))
    if kind == "refnum":
        return TypeInfo(kind, None, refnum=words[body] if len(words) > body else None)
    return TypeInfo(kind, None)


def read_type(lv_type: LVType | None) -> TypeInfo:
    """What ``lv_type`` records (``TypeInfo``); None is the empty variant's, of
    kind ``void``. A type the type string is needed for (an array, a cluster, an
    enum) and that has none raises ``NotImplementedError`` naming it."""
    if lv_type is None:
        return TypeInfo("void", None)
    kind = _kind_of(lv_type.code)
    if lv_type.type_string is None:
        if kind in ("array", "cluster", "enum"):
            raise NotImplementedError(
                f"the type {lv_type.name!r} (descriptor code {lv_type.code}) "
                "records no type string, so what it holds cannot be read"
            )
        return TypeInfo(
            kind, lv_type, number=_NUMBER_NAMES.get(lv_type.code)
        )
    words = [w & 0xFFFF for w in lv_type.type_string]
    if len(words) < 2 or words[0] != 2 * len(words):
        raise ValueError(
            f"the type string of {lv_type.name!r} has a size of "
            f"{words[0] if words else 0} bytes it cannot hold"
        )
    code = words[1] & 0xFF if len(words) > 1 else lv_type.code
    info = _read_words(words, code)
    return TypeInfo(**{**info.__dict__, "type": lv_type})


def type_default(lv_type: LVType) -> Any:
    """The default of a value of ``lv_type``: what a variant holding the type alone
    stands for (0, False, an empty string or array, a cluster of its members'
    defaults, an empty variant for a Variant; None for the rest)."""
    info = read_type(lv_type)
    if info.kind in ("number", "enum"):
        name = info.number or ""
        if "Float" in name:
            return 0.0
        if "Complex" in name:
            return 0j
        return 0
    if info.kind == "boolean":
        return False
    if info.kind == "string":
        return ""
    if info.kind == "array":
        return []
    if info.kind == "cluster":
        return tuple(type_default(member) for member in info.fields)
    if lv_type.code == _VARIANT_CODE:
        return Variant()
    return None
'''


def type_reader_source(scalar_constants: dict[int, str]) -> str:
    """The reader's source, its tables written from the encoder's;
    ``scalar_constants`` is the constant name of each scalar type by code."""
    number_names = {
        code.value: name for name, code in {**NUMBER_CODES, **ENUM_CODES}.items()
    }
    substitutions = {
        "NUMBER_NAMES": repr(number_names),
        "ENUM_CODES": repr(sorted(code.value for code in ENUM_CODES.values())),
        "BOOLEAN": repr(TD_FULL_TYPE.Boolean.value),
        "STRING": repr(TD_FULL_TYPE.String.value),
        "ARRAY": repr(TD_FULL_TYPE.Array.value),
        "CLUSTER": repr(TD_FULL_TYPE.Cluster.value),
        "REFNUM": repr(TD_FULL_TYPE.Refnum.value),
        "VARIANT": repr(TD_FULL_TYPE.LVVariant.value),
        "CLASS_REFNUM": repr(REFNUM_TYPE.UDClassInst.value),
        "MEASURE": repr(TD_FULL_TYPE.MeasureData.value),
        "TIMESTAMP_FLAVOR": repr(MEASURE_DATA_FLAVOR.TimeStamp.value),
        "VARIABLE_SIZE": repr(VARIABLE_SIZE),
        "SCALARS": ", ".join(f"{c}: {n}" for c, n in sorted(scalar_constants.items())),
    }
    text = _READER_TEMPLATE
    for key, value in sorted(substitutions.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(f"${key}", value)
    return text
