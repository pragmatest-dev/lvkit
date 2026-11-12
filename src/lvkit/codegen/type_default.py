"""The default value of a LabVIEW type inside a generated function.

LabVIEW's default of a class-typed wire, output, tunnel or field is the class's
default object (as a class constant is), and LabVIEW Object's is ``object()``.
``type_default_expr`` has no module to import a class into, so it gives ``None``
for a class; this module is the entry that has one (``CodeGenContext``), for
every default written inside a function body: a skipped node's output, an
unwired tunnel, an array's out-of-range element, and the rest.

A module-level variable that is seeded with a default (a state global: an
uninitialized shift register, a Feedback Node, a Local Variable's indicator)
cannot name a class: a module on an import cycle imports the other generated
modules inside its functions. Such a variable is seeded ``None`` when its type
holds a class (``state_seed``) and the function makes the object when it reads
``None`` (``default_if_unset``). A class-typed value is never "no object", so
``None`` there only means "not yet set".
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from lvkit.conversion_gap import ConversionGap
from lvkit.models import LVType, LVTypeKind, Terminal

from .ast_utils import (
    build_assign,
    cluster_default_expr,
    mark_default_object,
    parse_expr,
    saved_default_expr,
    type_default_expr,
)
from .gaps import gap_of, raise_statement

if TYPE_CHECKING:
    from .context import CodeGenContext

_CLASS_REF_TYPE = "UDClassInst"


class ClassNames(Protocol):
    """What names a class in the module a default is written in: the module's
    ``CodeGenContext``, or the class builder for a class's ``__init__``."""

    def class_expression(self, lv_type: LVType, what: str) -> str:
        """The Python name of the class ``lv_type`` names, imported."""
        ...


def is_class_type(lv_type: LVType | None) -> bool:
    """Whether ``lv_type`` is a LabVIEW class (LabVIEW Object included)."""
    return lv_type is not None and lv_type.ref_type == _CLASS_REF_TYPE


def holds_class(lv_type: LVType | None) -> bool:
    """Whether ``lv_type`` is a class or a cluster with a class in one of its
    fields (at any depth): its default needs a class object."""
    if lv_type is None:
        return False
    if is_class_type(lv_type):
        return True
    if lv_type.kind in (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF):
        return any(holds_class(f.type) for f in lv_type.fields or [])
    return False


def type_default(
    ctx: ClassNames, lv_type: LVType | None, what: str = "A default of"
) -> ast.expr:
    """The default value of ``lv_type``: ``type_default_expr``, with the class's
    default object (``Cls()``, imported) for a class type and for each class
    field of a cluster. A class that is not loaded is a ``ConversionGap`` whose
    reason starts with ``what``."""
    if lv_type is None:
        return ast.Constant(value=None)
    if is_class_type(lv_type):
        return mark_default_object(
            parse_expr(f"{ctx.class_expression(lv_type, what)}()")
        )
    if lv_type.kind in (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF):
        return cluster_default_expr(lv_type, lambda t: type_default(ctx, t, what))
    return type_default_expr(lv_type)


def control_default(ctx: CodeGenContext, control: Terminal) -> ast.expr:
    """``control_default_expr`` with the class default of ``type_default``: a
    control's saved default when that maps cleanly to its scalar type, else the
    type's default."""
    saved = saved_default_expr(control.default_value, control.lv_type)
    return saved if saved is not None else type_default(ctx, control.lv_type)


def state_seed(lv_type: LVType | None) -> ast.expr:
    """What a module-level variable of ``lv_type`` starts at: the type's default,
    or ``None`` when the type holds a class (``default_if_unset`` makes the
    object where the function reads it)."""
    if holds_class(lv_type):
        return ast.Constant(value=None)
    return type_default_expr(lv_type)


def default_if_unset(
    name: str, lv_type: LVType | None, ctx: CodeGenContext, label: str
) -> list[ast.stmt]:
    """``if name is None: name = <default>`` for a variable of ``lv_type`` that
    holds a class (empty for any other type); ``label`` names the variable in a
    gap. A class that is not loaded makes the branch raise the gap."""
    if not holds_class(lv_type):
        return []
    return [
        default_if_none(name, lambda: type_default(ctx, lv_type, label), ctx, label)
    ]


def default_if_none(
    name: str,
    default: Callable[[], ast.expr],
    ctx: CodeGenContext,
    label: str,
) -> ast.stmt:
    """``if name is None: name = default()``; when ``default`` is a gap (the
    class is not loaded) the branch raises it instead and the gap is recorded."""
    try:
        made: ast.stmt = build_assign(name, default())
    except ConversionGap as exc:
        gap = gap_of(exc, ctx.vi_name or "", None, label)
        ctx.gaps.append(gap)
        made = raise_statement(gap.message)
    return ast.If(test=parse_expr(f"{name} is None"), body=[made], orelse=[])
