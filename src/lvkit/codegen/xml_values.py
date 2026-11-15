"""A LabVIEW value as the LabVIEW XML schema's text, written and read with
``xml.etree.ElementTree``, from the value's type written out when the code is
generated (the same plain form as ``plain_values``: ``to_plain`` gives the
value as dicts, lists and scalars, ``from_plain`` builds the value back).

What the text looks like (every element is ``<Tag><Name>..</Name>..</Tag>``):
- a Boolean ``<Boolean>`` with ``<Val>`` 1 or 0, a string ``<String>``, an
  integer ``<I8>`` .. ``<U64>`` with its decimal digits, a double ``<DBL>``;
- a cluster ``<Cluster>`` with ``<Name>``, ``<NumElts>`` (the element count) and
  one element each, in order; an element with no label has an empty ``<Name>``;
- an array ``<Array>`` with ``<Name>``, one ``<Dimsize>`` per dimension and the
  elements in row-major order, each with an empty ``<Name>``; an empty array
  has one default element after its ``<Dimsize>`` 0.
The text has one element per line, lines end with CR LF and nothing is indented.
Only ``&``, ``<`` and ``>`` are escaped (NI's Flatten To XML page), which is what
ElementTree writes in text.

Sources (public; the NI pages do not print the schema file ``LVXMLSchema.xsd``):
- NI, Flatten To XML / Unflatten From XML pages, ``functions/flatten-to-xml.html``
  and ``functions/unflatten-from-xml.html`` (docs-be.ni.com): the schema, the
  escaping, the period as the only decimal separator, the tags' case and order.
- A DBL as NI's forum shows LabVIEW writing it: ``<DBL>\\r\\n<Name>Numeric</Name>``
  ``\\r\\n<Val>1.00000000000000</Val>\\r\\n</DBL>`` (forums.ni.com/t5/LabVIEW/
  save-and-read-control-value-using-xml/m-p/1063145), a 2016 posting with
  ``0.00000000000000`` and no indentation of the closing tags
  (forums.ni.com/t5/LabVIEW/Properly-Format-XML-Output-From-LabVIEW-for-Human-
  Eyes/m-p/3311432): 15 significant digits with the trailing zeros kept, which is
  C's ``%#.15g``. NI's book text for LabVIEW 7 shows ``3.14159`` (``%#.6g``);
  the later form is used.
- An I32 inside a cluster with an empty ``<Name>``, ``<NumElts>`` 4:
  forums.ni.com/t5/LabVIEW/XML-Timestamp-Interpretation/td-p/445511.
- The root ``<Name>`` is the label of the data wired to ``anything``, empty when
  it has none (forums.ni.com/t5/LabVIEW/XML-assign-name-to-a-string/td-p/411870;
  lavag.org/topic/10063-wire-lables).
- The ``<Dimsize>`` array layout, CR LF lines, NaN / Inf / -Inf, the empty array's
  one default element and ``<Boolean>`` ``1``/``0``: github.com/JaviLianes8/lvxml
  (README and GUIDE.md, its notes on files LabVIEW wrote).
Not documented anywhere found: the text of a SGL or EXT number (writing one is a
gap), SI
suffixes such as ``1k`` that LabVIEW reads in a number (an error here).
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from lvkit.models import LVType, LVTypeKind

from .plain_values import (
    PlainErrors,
    PlainOptions,
    UnsupportedPlainType,
    from_plain,
    plain_fields,
    plain_labels,
    scalar_kind,
    to_plain,
)
from .type_default import type_default

if TYPE_CHECKING:
    from .context import CodeGenContext

_INTEGER_TAGS = {
    "NumInt8": "I8",
    "NumInt16": "I16",
    "NumInt32": "I32",
    "NumInt64": "I64",
    "NumUInt8": "U8",
    "NumUInt16": "U16",
    "NumUInt32": "U32",
    "NumUInt64": "U64",
}
_FLOAT_TAGS = {
    "NumFloat32": "SGL",
    "UnitFloat32": "SGL",
    "NumFloat64": "DBL",
    "UnitFloat64": "DBL",
    "NumFloatExt": "EXT",
    "UnitFloatExt": "EXT",
}
# The tags of the schema this writer and reader know; a tag outside them is
# "not recognized" (1103), one of them in the wrong place "does not match the
# wired type" (1106).
_KNOWN_TAGS = (
    *_INTEGER_TAGS.values(),
    "SGL",
    "DBL",
    "EXT",
    "CSG",
    "CDB",
    "CXT",
    "Boolean",
    "String",
    "Path",
    "Array",
    "Cluster",
    "EW",
    "Refnum",
)
# A number as LabVIEW reads it (period only), NaN and the infinities in any case.
_FLOAT_TEXT = r"' *(?:[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?|[+-]?inf|nan) *'"

# Every tag boundary is a line break (CR LF) except the one inside an empty
# <Name></Name> or <Val></Val>; text holds no ``><`` because ElementTree writes
# ``>`` in text as ``&gt;``.
_FINISH = """\
{target} = re.sub(
    r'(?<!<Name)(?<!<Val)><',
    '>\\r\\n<',
    ET.tostring({root}, encoding='unicode', short_empty_elements=False),
)
"""


def scalar_tag(lv_type: LVType) -> str:
    """The schema tag of a scalar type."""
    kind = scalar_kind(lv_type)
    underlying = lv_type.underlying_type or ""
    if kind == "bool":
        return "Boolean"
    if kind == "string":
        return "String"
    if kind == "int":
        return _INTEGER_TAGS[underlying]
    if underlying in _FLOAT_TAGS:
        return _FLOAT_TAGS[underlying]
    raise UnsupportedPlainType(f"{underlying} (no XML tag known)")


class _Source:
    """The source lines of the statements being written."""

    def __init__(self, ctx: CodeGenContext, node_id: str) -> None:
        self.ctx = ctx
        self.node_id = node_id
        self.lines: list[str] = []
        self.level = 0

    def add(self, text: str) -> None:
        self.lines.append("    " * self.level + text)

    def fresh(self, base: str) -> str:
        return self.ctx.make_output_var(base, self.node_id)

    def block(self, header: str) -> _Block:
        self.add(header)
        return _Block(self)

    def statements(self) -> list[ast.stmt]:
        return ast.parse("\n".join(self.lines)).body


class _Block:
    def __init__(self, source: _Source) -> None:
        self.source = source

    def __enter__(self) -> None:
        self.source.level += 1

    def __exit__(self, *exc: object) -> None:
        self.source.level -= 1


class _Writer(_Source):
    def element(self, parent: str, tag: str, name: str) -> str:
        element = self.fresh("element")
        self.add(f"{element} = ET.SubElement({parent}, {tag!r})")
        self.add(f"ET.SubElement({element}, 'Name').text = {name!r}")
        return element

    def write(self, source: str, lv_type: LVType, parent: str, name: str) -> None:
        """Add the element of the plain value ``source`` to ``parent``."""
        if lv_type.kind == LVTypeKind.ARRAY and lv_type.element_type is not None:
            self.array(
                source, lv_type.element_type, lv_type.dimensions or 1, parent, name
            )
        elif lv_type.kind in (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF):
            self.cluster(source, lv_type, parent, name)
        else:
            self.scalar(source, lv_type, parent, name)

    def scalar(self, source: str, lv_type: LVType, parent: str, name: str) -> None:
        tag = scalar_tag(lv_type)
        kind = scalar_kind(lv_type)
        value = self.fresh("value")
        self.add(f"{value} = {source}")
        if kind == "bool":
            text = f"'1' if {value} else '0'"
        elif kind == "string":
            text = value
        elif kind == "int":
            text = f"str({value})"
        elif tag == "DBL":
            self.ctx.add_import("import math")
            text = (
                f"('%#.15g' % {value} if math.isfinite({value}) else "
                f"'NaN' if {value} != {value} else "
                f"'Inf' if {value} > 0 else '-Inf')"
            )
        else:
            raise UnsupportedPlainType(
                f"a {tag} number (NI does not document its text)"
            )
        element = self.element(parent, tag, name)
        self.add(f"ET.SubElement({element}, 'Val').text = {text}")

    def cluster(self, source: str, lv_type: LVType, parent: str, name: str) -> None:
        fields = plain_fields(lv_type, self.ctx)
        labels = plain_labels(lv_type, self.ctx)
        element = self.element(parent, "Cluster", name)
        self.add(f"ET.SubElement({element}, 'NumElts').text = {str(len(fields))!r}")
        for (key, field_type), label in zip(fields, labels):
            self.write(f"{source}[{key!r}]", field_type, element, label)

    def array(
        self, source: str, element_type: LVType, rank: int, parent: str, name: str
    ) -> None:
        array = self.element(parent, "Array", name)
        level = self.fresh("level")
        flat = self.fresh("flat")
        self.add(f"{level} = {source}")
        self.add(f"{flat} = {source}")
        step = self.fresh("step")
        with self.block(f"for {step} in range({rank}):"):
            self.add(f"ET.SubElement({array}, 'Dimsize').text = str(len({level}))")
            self.add(f"{level} = {level}[0] if {level} else []")
        if rank > 1:
            row = self.fresh("row")
            item = self.fresh("item")
            with self.block(f"for {step} in range({rank - 1}):"):
                self.add(f"{flat} = [{item} for {row} in {flat} for {item} in {row}]")
        member = self.fresh("member")
        with self.block(f"for {member} in {flat}:"):
            self.write(member, element_type, array, "")
        with self.block(f"if not {flat}:"):
            default = to_plain(
                ast.unparse(type_default(self.ctx, element_type)),
                element_type,
                self.ctx,
            )
            self.write(default, element_type, array, "")


def write_xml(
    source: str,
    lv_type: LVType,
    ctx: CodeGenContext,
    node_id: str,
    target: str,
    name: str = "",
) -> tuple[list[ast.stmt], list[str]]:
    """The statements that give ``target`` the XML string of ``source``, a
    variable holding a value of ``lv_type`` whose wire is labelled ``name``, and
    the imports they need (``UnsupportedPlainType`` for a type with no XML
    written)."""
    writer = _Writer(ctx, node_id)
    value = writer.fresh("plain")
    holder = writer.fresh("holder")
    root = writer.fresh("root")
    writer.add(f"{value} = {to_plain(source, lv_type, ctx)}")
    writer.add(f"{holder} = ET.Element('Holder')")
    writer.write(value, lv_type, holder, name)
    writer.add(f"{root} = {holder}[0]")
    writer.lines.extend(_FINISH.format(root=root, target=target).splitlines())
    return writer.statements(), [
        "import re",
        "import xml.etree.ElementTree as ET",
    ]


class _Reader(_Source):
    def __init__(self, ctx: CodeGenContext, node_id: str, errors: XmlErrors) -> None:
        super().__init__(ctx, node_id)
        self.errors = errors

    def check_tag(self, element: str, tag: str) -> None:
        errors = self.errors
        with self.block(f"if {element}.tag != {tag!r}:"):
            known = tuple(_KNOWN_TAGS)
            with self.block(f"if {element}.tag not in {known!r}:"):
                self.add(
                    f"raise {errors.tag_unknown}('the tag ' + repr({element}.tag)"
                    " + ' is not in the LabVIEW XML schema')"
                )
            self.add(
                f"raise {errors.tag_mismatch}('expected <{tag}> but found <' + "
                f"{element}.tag + '>')"
            )

    def check_children(self, element: str, expected: str, what: str) -> None:
        """Raise the unexpected tag error unless the tags of ``element``'s
        children are ``expected`` (a Python tuple expression)."""
        with self.block(f"if tuple(c.tag for c in {element}) != {expected}:"):
            self.add(
                f"raise {self.errors.tag_unexpected}('{what} has the tags ' + "
                f"str(tuple(c.tag for c in {element})) + ' instead of ' + "
                f"str({expected}))"
            )

    def read(self, element: str, lv_type: LVType, target: str) -> None:
        """Statements that give ``target`` the plain value of ``element``."""
        if lv_type.kind == LVTypeKind.ARRAY and lv_type.element_type is not None:
            self.array(element, lv_type.element_type, lv_type.dimensions or 1, target)
        elif lv_type.kind in (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF):
            self.cluster(element, lv_type, target)
        else:
            self.scalar(element, lv_type, target)

    def scalar(self, element: str, lv_type: LVType, target: str) -> None:
        tag = scalar_tag(lv_type)
        kind = scalar_kind(lv_type)
        self.check_tag(element, tag)
        self.check_children(element, "('Name', 'Val')", f"<{tag}>")
        text = self.fresh("text")
        self.add(f"{text} = {element}[1].text or ''")
        if kind == "string":
            self.add(f"{target} = {text}")
            return
        self.ctx.add_import("import re")
        pattern = {
            "bool": r"' *\d+ *'",
            "int": r"' *-?\d+ *'",
            "float": _FLOAT_TEXT,
        }[kind]
        flags = ", re.I" if kind == "float" else ""
        with self.block(f"if not re.fullmatch(r{pattern}, {text}{flags}):"):
            self.add(
                f"raise {self.errors.value}('the text ' + repr({text}) + "
                f"' is not a value of <{tag}>')"
            )
        convert = {
            "bool": f"int({text}) != 0",
            "int": f"int({text})",
            "float": f"float({text})",
        }[kind]
        self.add(f"{target} = {convert}")

    def cluster(self, element: str, lv_type: LVType, target: str) -> None:
        fields = plain_fields(lv_type, self.ctx)
        count = len(fields)
        self.check_tag(element, "Cluster")
        children = self.fresh("children")
        self.add(f"{children} = list({element})")
        with self.block(
            f"if len({children}) != {count + 2} or "
            f"tuple(c.tag for c in {children}[:2]) != ('Name', 'NumElts'):"
        ):
            self.add(
                f"raise {self.errors.tag_unexpected}('<Cluster> needs <Name>, "
                f"<NumElts> and {count} elements, it has the tags ' + "
                f"str(tuple(c.tag for c in {children})))"
            )
        with self.block(f"if ({children}[1].text or '').strip() != {str(count)!r}:"):
            self.add(
                f"raise {self.errors.tag_unexpected}('<NumElts> is ' + "
                f"repr({children}[1].text) + ', the cluster has {count} elements')"
            )
        self.add(f"{target} = {{}}")
        for position, (key, field_type) in enumerate(fields):
            member = self.fresh("member")
            self.read(f"{children}[{position + 2}]", field_type, member)
            self.add(f"{target}[{key!r}] = {member}")

    def array(self, element: str, element_type: LVType, rank: int, target: str) -> None:
        self.check_tag(element, "Array")
        self.ctx.add_import("import math")
        self.ctx.add_import("import re")
        children = self.fresh("children")
        dims = self.fresh("dims")
        self.add(f"{children} = list({element})")
        expected = repr(("Name", *(["Dimsize"] * rank)))
        with self.block(
            f"if tuple(c.tag for c in {children}[:{rank + 1}]) != {expected}:"
        ):
            self.add(
                f"raise {self.errors.tag_unexpected}('<Array> needs <Name> and "
                f"{rank} <Dimsize>, it has the tags ' + "
                f"str(tuple(c.tag for c in {children})))"
            )
        self.add(f"{dims} = []")
        size = self.fresh("size")
        with self.block(f"for {size} in {children}[1:{rank + 1}]:"):
            with self.block(f"if not re.fullmatch(r' *\\d+ *', {size}.text or ''):"):
                self.add(
                    f"raise {self.errors.value}('the text ' + repr({size}.text) + "
                    "' is not a <Dimsize>')"
                )
            self.add(f"{dims}.append(int({size}.text))")
        count = self.fresh("count")
        self.add(f"{count} = math.prod({dims})")
        with self.block(f"if len({children}) - {rank + 1} != ({count} or 1):"):
            self.add(
                f"raise {self.errors.tag_unexpected}('<Array> with <Dimsize> ' + "
                f"str({dims}) + ' needs ' + str({count} or 1) + ' elements, it has ' "
                f"+ str(len({children}) - {rank + 1}))"
            )
        flat = self.fresh("flat")
        self.add(f"{flat} = []")
        item = self.fresh("item")
        with self.block(f"for {item} in {children}[{rank + 1}:{rank + 1}+{count}]:"):
            member = self.fresh("member")
            self.read(item, element_type, member)
            self.add(f"{flat}.append({member})")
        if rank > 1:
            level = self.fresh("level")
            group = self.fresh("group")
            with self.block(f"for {level} in range({rank - 1}, 0, -1):"):
                self.add(
                    f"{flat} = [{flat}[{group} * {dims}[{level}]:"
                    f"({group} + 1) * {dims}[{level}]] "
                    f"for {group} in range(math.prod({dims}[:{level}]))]"
                )
        self.add(f"{target} = {flat}")


class XmlErrors:
    """The names of the exception classes the reader raises."""

    def __init__(
        self,
        empty: str,
        tag_unknown: str,
        tag_unexpected: str,
        tag_mismatch: str,
        value: str,
    ) -> None:
        self.empty = empty
        self.tag_unknown = tag_unknown
        self.tag_unexpected = tag_unexpected
        self.tag_mismatch = tag_mismatch
        self.value = value


def read_xml(
    text: str,
    lv_type: LVType,
    default: str,
    target: str,
    ctx: CodeGenContext,
    node_id: str,
    errors: XmlErrors,
) -> tuple[list[ast.stmt], list[str]]:
    """The statements that give ``target`` the value of ``lv_type`` read from
    the XML string ``text`` (an expression); ``default`` is the expression of
    the ``type`` input's value. ``ET.ParseError`` (a start tag with no end tag,
    XML that is not well formed) is left to the caller's error table."""
    reader = _Reader(ctx, node_id, errors)
    root = reader.fresh("root")
    plain = reader.fresh("plain")
    document = reader.fresh("document")
    reader.add(f"{document} = {text}")
    with reader.block(f"if not {document}.strip():"):
        reader.add(f"raise {errors.empty}('the xml string is empty')")
    reader.add(f"{root} = ET.fromstring({document})")
    reader.read(root, lv_type, plain)
    statements = reader.statements()
    options = PlainOptions(
        null_to_default="False",
        strict="False",
        errors=PlainErrors(
            type_mismatch=errors.tag_mismatch,
            element_missing=errors.tag_unexpected,
            extra_items=errors.tag_unexpected,
            out_of_range=errors.value,
            dimensions=errors.tag_unexpected,
            ragged=errors.tag_unexpected,
        ),
    )
    statements.extend(
        from_plain(plain, lv_type, default, target, ctx, node_id, options)
    )
    return statements, ["import xml.etree.ElementTree as ET"]
