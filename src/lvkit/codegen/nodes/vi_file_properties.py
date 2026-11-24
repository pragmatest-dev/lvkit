"""The VI Server properties of the VI class that are facts the VI's file records:
Execution:Is Reentrant, Execution:Priority, VI Type, Help:Document Web URL, and
the two that follow from reentrancy, Is Clone VI and VI Clone Name.

Each generated function carries them as attributes set right after its ``def``
beside ``function.vi_name`` (``vi_file_property_statements``), with the values NI's
pages list: a read of the property on a VI reference is the attribute of the
function the reference holds, and on the default VI object the attribute of the
VI's own function (``default_objects.json``). The first four are constants of the
file (a write of Help:Document Web URL sets its attribute, NI: "Settable when the
VI is running: Yes"; the others are "No", so a write stays a gap).

- ``is_reentrant``: True for a VI saved as reentrant (``ExecutionProps.reentrancy``).
- ``priority``: NI's number, 1 background, 2 normal, 3 above normal, 4 high,
  5 time critical, 6 subroutine (execpriority.html).
- ``vi_type``: NI's number, 1 Standard VI, 2 Control VI, 3 Global VI, 4 Polymorphic
  VI (vitype.html); not set for a file kind that list has no number for.
- ``help_url``: the file's Help Website URL, '' when it records none.
- Is Clone VI (isclonevi.html: "Returns TRUE if the VI is a clone of a reentrant
  VI") reads ``is_reentrant``: every call of a reentrant function is its own run,
  which is what a clone is.
- VI Clone Name (clonename.html: "Name of the clone of a reentrant VI. Returns an
  error if the VI is not a clone.") reads ``clone_name``, the VI's ``vi_name``, set
  for a reentrant VI only; a function that is not reentrant has no such attribute,
  and reading it fails.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from lvkit.graph.models import Priority, Reentrancy
from lvkit.models import PropertyDef, VIKind

from ..ast_utils import parse_expr, parse_stmt
from ..context import CodeGenContext

IS_REENTRANT_ATTRIBUTE = "is_reentrant"
PRIORITY_ATTRIBUTE = "priority"
VI_TYPE_ATTRIBUTE = "vi_type"
HELP_URL_ATTRIBUTE = "help_url"
CLONE_NAME_ATTRIBUTE = "clone_name"

# NI's value of each priority (execpriority.html).
_PRIORITY_NUMBER = {
    Priority.BACKGROUND: 1,
    Priority.NORMAL: 2,
    Priority.ABOVE_NORMAL: 3,
    Priority.HIGH: 4,
    Priority.TIME_CRITICAL: 5,
    Priority.SUBROUTINE: 6,
}

# NI's value of each VI type the file records (vitype.html).
_VI_TYPE_NUMBER = {
    VIKind.ORDINARY: 1,
    VIKind.CONTROL: 2,
    VIKind.GLOBAL: 3,
    VIKind.POLYMORPHIC: 4,
}


@dataclass(frozen=True)
class FileProperty:
    """A property of the VI class read from the function's attribute: the code a
    Property Node records for it and whether a write sets the attribute."""

    code: int
    attribute: str
    writable: bool = False


FILE_PROPERTIES = (
    FileProperty(525, IS_REENTRANT_ATTRIBUTE),  # Exec.IsReentrant
    FileProperty(526, PRIORITY_ATTRIBUTE),  # Exec.Priority
    FileProperty(562, VI_TYPE_ATTRIBUTE),  # VIType
    FileProperty(645, CLONE_NAME_ATTRIBUTE),  # Clone Name
    FileProperty(646, IS_REENTRANT_ATTRIBUTE),  # IsCloneVI
    FileProperty(698, HELP_URL_ATTRIBUTE, writable=True),  # Help.URL
)
_BY_CODE = {row.code: row for row in FILE_PROPERTIES}


def file_property(prop: PropertyDef) -> FileProperty | None:
    """The listed property a Property Node row is, by its code; None for any other
    row."""
    if len(prop.segments) != 1:
        return None
    return _BY_CODE.get(prop.segments[0].code)


def read(row: FileProperty, reference: str) -> ast.expr:
    """The value of the property for the VI whose function is ``reference``."""
    return parse_expr(f"{reference}.{row.attribute}")


def write(row: FileProperty, reference: str, value: str) -> ast.stmt:
    """Set the property of the VI whose function is ``reference``."""
    return parse_stmt(f"{reference}.{row.attribute} = {value}")


def vi_file_property_statements(
    ctx: CodeGenContext, key: str, target: str
) -> list[ast.stmt]:
    """``target.is_reentrant = ...``, ``priority``, ``vi_type``, ``help_url`` and,
    for a reentrant VI, ``clone_name`` for the VI ``key``, from its file."""
    properties = ctx.graph.get_vi_properties(key) if ctx.graph is not None else None
    reentrant = (
        properties is not None
        and properties.execution.reentrancy is not Reentrancy.NON_REENTRANT
    )
    priority = Priority.NORMAL if properties is None else properties.execution.priority
    values: list[tuple[str, ast.expr]] = [
        (IS_REENTRANT_ATTRIBUTE, ast.Constant(value=reentrant)),
        (PRIORITY_ATTRIBUTE, ast.Constant(value=_PRIORITY_NUMBER[priority])),
    ]
    if properties is not None:
        kind = _vi_kind(properties.vi_type)
        if kind is not None:
            values.append(
                (VI_TYPE_ATTRIBUTE, ast.Constant(value=_VI_TYPE_NUMBER[kind]))
            )
    values.append(
        (
            HELP_URL_ATTRIBUTE,
            ast.Constant(value="" if properties is None else properties.help_url),
        )
    )
    if reentrant:
        values.append((CLONE_NAME_ATTRIBUTE, parse_expr(f"{target}.vi_name")))
    return [
        ast.Assign(
            targets=[
                ast.Attribute(value=parse_expr(target), attr=attribute, ctx=ast.Store())
            ],
            value=value,
        )
        for attribute, value in values
    ]


def _vi_kind(label: str | None) -> VIKind | None:
    """The file kind whose LabVIEW name is ``label`` (``VIProperties.vi_type``);
    None for a kind with no member."""
    return next((kind for kind in VIKind if kind.label == label), None)
