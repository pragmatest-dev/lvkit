"""Build the field annotations and ``__init__`` from a class's private data."""

from __future__ import annotations

import ast

from lvkit.models import ClusterField, TypeNamer

from ..ast_utils import default_value_expr
from ..class_model import ClassModel
from ..gaps import raise_statement
from ..type_fields import field_annotations


class _InitBuilderMixin:
    """The attributes a class declares and the ``__init__`` that sets them."""

    def _field_annotations(
        self,
        model: ClassModel,
        fields: list[ClusterField],
        spell: TypeNamer | None,
    ) -> list[ast.stmt]:
        """One annotated attribute per private data field the class declares."""
        return field_annotations(model.attributes.fields, fields, spell)

    def _build_init(
        self,
        model: ClassModel,
        fields: list[ClusterField],
        has_base: bool,
        unavailable: str | None,
    ) -> ast.FunctionDef:
        """``__init__``: the base's, then each field the class declares at its
        default; ``unavailable`` (a gap) makes it raise instead."""
        body: list[ast.stmt] = []
        if unavailable is not None:
            body.append(raise_statement(unavailable))
        else:
            if has_base:
                body.append(ast.parse("super().__init__()").body[0])
            for attribute, field in zip(model.attributes.fields, fields, strict=True):
                body.append(
                    ast.Assign(
                        targets=[
                            ast.Attribute(
                                value=ast.Name(id="self", ctx=ast.Load()),
                                attr=attribute,
                                ctx=ast.Store(),
                            )
                        ],
                        value=default_value_expr(field.type),
                    )
                )
        return ast.FunctionDef(
            name="__init__",
            args=ast.arguments(
                posonlyargs=[],
                args=[ast.arg(arg="self", annotation=None)],
                vararg=None,
                kwonlyargs=[],
                kw_defaults=[],
                kwarg=None,
                defaults=[],
            ),
            body=body or [ast.Pass()],
            decorator_list=[],
            returns=ast.Constant(value=None),
        )
