"""Build @property and setter methods for the property accessors that have logic."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from lvkit.class_property import PropertyRole
from lvkit.structure import LVMethod

from ..class_attributes import Accessor

if TYPE_CHECKING:
    from lvkit.graph import InMemoryVIGraph


class _PropertyBuilderMixin:
    """The Python property of a property folder whose accessors have logic."""

    if TYPE_CHECKING:
        # Declared on the composed ClassBuilder (core.py) and the sibling
        # mixins; type-check only, so pyright resolves cross-mixin access.
        _graph: InMemoryVIGraph | None

        def _build_instance_method(
            self,
            method: LVMethod,
            class_name: str,
            prefix: str = "",
            name: str | None = None,
        ) -> ast.FunctionDef: ...

    def _build_properties(
        self,
        class_name: str,
        members: list[tuple[LVMethod, Accessor]],
    ) -> list[ast.stmt]:
        """A ``@property`` for the read VI and a ``@<name>.setter`` for the write
        VI of each property, each written from its own diagram; ``members`` lists
        the property VIs in the order the class file lists them. A property with
        no read VI reads as an ``AttributeError``."""
        stmts: list[ast.stmt] = []
        by_property: dict[str, dict[PropertyRole, tuple[LVMethod, Accessor]]] = {}
        for method, accessor in members:
            assert accessor.role is not None
            by_property.setdefault(accessor.property_name, {})[accessor.role] = (
                method,
                accessor,
            )

        for roles in by_property.values():
            getter = roles.get(PropertyRole.GETTER)
            setter = roles.get(PropertyRole.SETTER)
            anchor = getter or setter
            assert anchor is not None
            name = anchor[1].python_name
            if getter is not None:
                stmts.append(self._property_def(getter[0], class_name, name, None))
            else:
                stmts.append(_write_only_getter(name))
            if setter is not None:
                stmts.append(self._property_def(setter[0], class_name, name, name))
        return stmts

    def _property_def(
        self,
        method: LVMethod,
        class_name: str,
        name: str,
        setter_of: str | None,
    ) -> ast.FunctionDef:
        """The method of a property VI, named ``name`` and decorated as the
        property (or, with ``setter_of``, as that property's setter)."""
        func = self._build_instance_method(method, class_name, name=name)
        if setter_of is None:
            func.decorator_list = [ast.Name(id="property", ctx=ast.Load())]
        else:
            func.decorator_list = [
                ast.Attribute(
                    value=ast.Name(id=setter_of, ctx=ast.Load()),
                    attr="setter",
                    ctx=ast.Load(),
                )
            ]
        return func


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
