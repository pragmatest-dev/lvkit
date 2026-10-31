"""Build the __init__ method from LabVIEW class private data fields."""

from __future__ import annotations

import ast

from lvkit.structure import LVClass

from ..class_attributes import unclaimed_field_attributes


class _InitBuilderMixin:
    """Builds ``__init__`` from private data fields and default values."""

    def _build_init(
        self,
        lvclass: LVClass,
        has_parent: bool,
        attributes: list[str] | None = None,
    ) -> ast.FunctionDef:
        """Build __init__ method from private data fields; ``attributes`` is the
        attribute each field has (see ``class_attributes``), by default the
        attribute of a field no property names."""
        body: list[ast.stmt] = []

        # Call parent __init__ if there's a parent class
        if has_parent:
            body.append(
                ast.Expr(
                    value=ast.Call(
                        func=ast.Attribute(
                            value=ast.Call(
                                func=ast.Name(id="super", ctx=ast.Load()),
                                args=[],
                                keywords=[],
                            ),
                            attr="__init__",
                            ctx=ast.Load(),
                        ),
                        args=[],
                        keywords=[],
                    )
                )
            )

        fields = lvclass.private_data_fields
        attr_names = attributes or unclaimed_field_attributes(
            [f.name for f in fields]
        )

        for field, attr_name in zip(fields, attr_names, strict=True):
            body.append(
                ast.Assign(
                    targets=[
                        ast.Attribute(
                            value=ast.Name(id="self", ctx=ast.Load()),
                            attr=attr_name,
                            ctx=ast.Store(),
                        )
                    ],
                    value=self._get_default_for_type(field.python_type),
                )
            )

        # Ensure body is not empty
        if not body:
            body.append(ast.Pass())

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
            body=body,
            decorator_list=[],
            returns=ast.Constant(value=None),
        )

    def _get_default_for_type(self, python_type: str) -> ast.expr:
        """Get default value AST for a Python type."""
        type_defaults = {
            "str": ast.Constant(value=""),
            "int": ast.Constant(value=0),
            "float": ast.Constant(value=0.0),
            "bool": ast.Constant(value=False),
            "list": ast.List(elts=[], ctx=ast.Load()),
            "dict": ast.Dict(keys=[], values=[]),
            "Path": ast.Call(
                func=ast.Name(id="Path", ctx=ast.Load()),
                args=[],
                keywords=[],
            ),
        }
        return type_defaults.get(python_type, ast.Constant(value=None))
