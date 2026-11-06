"""Build the Python property of each property folder of a class.

The read VI is the property's getter and the write VI its setter, each written
from its own diagram. A property with no write VI has no setter (assigning it
raises ``AttributeError``); one with no read VI is ``@property().setter`` (reading
it raises ``AttributeError``). A property the class it descends from has too (an
override of the same long name) starts from the inherited one,
``@Parent.name.getter`` / ``@Parent.name.setter``, so the side the class does not
override stays the parent's."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from lvkit.class_property import PropertyRole

from ..class_attributes import Accessor
from ..class_model import ClassModel, MemberKind, MethodShape
from ..import_resolver import NameImport

_SIDES = {PropertyRole.GETTER: "getter", PropertyRole.SETTER: "setter"}


class _PropertyBuilderMixin:
    """The Python property of each property folder."""

    if TYPE_CHECKING:
        _model: ClassModel

        def _build_method(
            self,
            shape: MethodShape,
            name: str | None = None,
            decorators: list[ast.expr] | None = None,
            property_accessor: bool = False,
        ) -> ast.FunctionDef: ...

    def _build_properties(
        self, members: list[tuple[MethodShape, Accessor]], base: NameImport | None
    ) -> list[ast.stmt]:
        """A getter for the read VI and a setter for the write VI of each property;
        ``members`` lists the property VIs in the order the class file lists them,
        ``base`` is the class this one descends from."""
        stmts: list[ast.stmt] = []
        by_property: dict[str, dict[PropertyRole, MethodShape]] = {}
        names: dict[str, str] = {}
        for shape, accessor in members:
            by_property.setdefault(accessor.property_name, {})[accessor.role] = shape
            names[accessor.property_name] = accessor.python_name

        for property_name, roles in by_property.items():
            name = names[property_name]
            # The property the next getter or setter is made from.
            source: ast.expr | None = None
            if base is not None and self._model.inherited.get(name) is (
                MemberKind.PROPERTY
            ):
                source = _attribute(_name(base.name), name)
            for role in (PropertyRole.GETTER, PropertyRole.SETTER):
                shape = roles.get(role)
                if shape is None:
                    continue
                if source is not None:
                    decorator: ast.expr = _attribute(source, _SIDES[role])
                elif role is PropertyRole.GETTER:
                    decorator = _name("property")
                else:
                    decorator = _attribute(
                        ast.Call(func=_name("property"), args=[], keywords=[]),
                        "setter",
                    )
                stmts.append(
                    self._build_method(
                        shape,
                        name=name,
                        decorators=[decorator],
                        property_accessor=True,
                    )
                )
                source = _name(name)
        return stmts


def _name(identifier: str) -> ast.Name:
    return ast.Name(id=identifier, ctx=ast.Load())


def _attribute(value: ast.expr, attr: str) -> ast.Attribute:
    return ast.Attribute(value=value, attr=attr, ctx=ast.Load())
