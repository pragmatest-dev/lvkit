"""Build the __init__ method from LabVIEW class private data fields."""

from __future__ import annotations

import ast

from lvkit.structure import LVClass

from ..ast_utils import to_var_name


class _InitBuilderMixin:
    """Builds ``__init__`` from private data fields and default values."""

    def _build_init(
        self,
        lvclass: LVClass,
        parent_class_name: str | None,
    ) -> ast.FunctionDef:
        """Build __init__ method from private data fields."""
        body: list[ast.stmt] = []

        # Call parent __init__ if there's a parent class
        if parent_class_name:
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

        # Initialize private data fields as instance attributes
        # Accessor scope determines visibility:
        #   public accessor or no accessor → self.field
        #   protected accessor → self._field
        #   private accessor → self.__field
        accessor_scopes: dict[str, str] = {}
        for m in lvclass.methods:
            if m.is_accessor and m.accessor_field:
                key = to_var_name(m.accessor_field)
                # Use most restrictive scope if multiple accessors
                if key not in accessor_scopes or m.scope != "public":
                    accessor_scopes[key] = m.scope

        for field in lvclass.private_data_fields:
            # Skip placeholder/invalid field names
            if not field.name or field.name.lower() == "none":
                continue

            var_name = to_var_name(field.name)
            scope = accessor_scopes.get(var_name, "public")

            if scope == "private":
                attr_name = "__" + var_name
            elif scope == "protected":
                attr_name = "_" + var_name
            else:
                attr_name = var_name

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
