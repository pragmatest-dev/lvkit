"""Build @property and setter methods for the property accessors that have logic."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from lvkit.class_property import PropertyRole

from ..class_attributes import Accessor
from ..class_model import ClassModel, MethodShape


class _PropertyBuilderMixin:
    """The Python property of a property folder whose accessors have logic."""

    if TYPE_CHECKING:
        _model: ClassModel

        def _build_method(
            self,
            shape: MethodShape,
            name: str | None = None,
            decorators: list[ast.expr] | None = None,
        ) -> ast.FunctionDef: ...

    def _build_properties(
        self, members: list[tuple[MethodShape, Accessor]]
    ) -> list[ast.stmt]:
        """A ``@property`` for the read VI and a ``@<name>.setter`` for the write
        VI of each property, each written from its own diagram; ``members`` lists
        the property VIs in the order the class file lists them. A property with
        no read VI reads as an ``AttributeError``."""
        stmts: list[ast.stmt] = []
        by_property: dict[str, dict[PropertyRole, tuple[MethodShape, Accessor]]] = {}
        for shape, accessor in members:
            assert accessor.role is not None
            by_property.setdefault(accessor.property_name, {})[accessor.role] = (
                shape,
                accessor,
            )

        for roles in by_property.values():
            getter = roles.get(PropertyRole.GETTER)
            setter = roles.get(PropertyRole.SETTER)
            anchor = getter or setter
            assert anchor is not None
            name = anchor[1].python_name
            if getter is not None:
                stmts.append(
                    self._build_method(
                        getter[0],
                        name=name,
                        decorators=[ast.Name(id="property", ctx=ast.Load())],
                    )
                )
            else:
                stmts.append(_write_only_getter(name))
            if setter is not None:
                stmts.append(
                    self._build_method(
                        setter[0],
                        name=name,
                        decorators=[
                            ast.Attribute(
                                value=ast.Name(id=name, ctx=ast.Load()),
                                attr="setter",
                                ctx=ast.Load(),
                            )
                        ],
                    )
                )
        return stmts


def _write_only_getter(name: str) -> ast.FunctionDef:
    """The read side of a property that only a write VI defines."""
    return ast.FunctionDef(
        name=name,
        args=ast.arguments(
            posonlyargs=[],
            args=[ast.arg(arg="self", annotation=None)],
            vararg=None,
            kwonlyargs=[],
            kw_defaults=[],
            kwarg=None,
            defaults=[],
        ),
        body=[
            ast.Raise(
                exc=ast.Call(
                    func=ast.Name(id="AttributeError", ctx=ast.Load()),
                    args=[ast.Constant(value=f"{name} is write-only")],
                    keywords=[],
                ),
                cause=None,
            )
        ],
        decorator_list=[ast.Name(id="property", ctx=ast.Load())],
        returns=ast.Name(id="NoReturn", ctx=ast.Load()),
    )
