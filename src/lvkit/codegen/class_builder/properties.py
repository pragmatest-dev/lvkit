"""Build @property accessors from LabVIEW getter/setter method pairs."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lvkit.class_property import PropertyRole, property_role
from lvkit.structure import LVClass, LVMethod

from ..ast_utils import private_attribute_names, to_var_name
from .property_field import accessed_attribute

if TYPE_CHECKING:
    from lvkit.graph import InMemoryVIGraph
    from lvkit.graph.models import VIContext

    from .config import ClassConfig


@dataclass(frozen=True)
class PropertyAccessor:
    """A property VI written as part of a Python property: what it does and the
    private attribute it moves."""

    method: LVMethod
    role: PropertyRole
    attribute: str


class _PropertyBuilderMixin:
    """Property classification and @property generation."""

    if TYPE_CHECKING:
        # Populated on the composed ClassBuilder (core.py); declared here
        # (type-check only, zero runtime effect) so pyright can resolve
        # cross-mixin access.
        _method_contexts: dict[str, VIContext]
        _graph: InMemoryVIGraph | None
        config: ClassConfig

    def _scope_prefix(self, scope: str) -> str:
        """The prefix a method or property of ``scope`` has in Python: none for
        public, ``_`` for protected, ``__`` for private (and community)."""
        if scope == "public":
            return ""
        if scope == "protected":
            return self.config.protected_prefix
        return self.config.private_prefix

    def _property_accessors(self, lvclass: LVClass) -> list[PropertyAccessor]:
        """The accessors of the class's properties that are written as Python
        properties.

        A property of one scope becomes a Python property when every VI in its
        folder is a getter or a setter (by its connector pane) of the one private
        data field its diagram moves (at most one of each); a VI of the folder
        whose diagram does more than move that field is an ordinary method written
        from its diagram. A property whose Python name is the attribute of a
        private data field is ordinary methods too, as the property would hide
        the field."""
        groups: dict[tuple[str, str], list[LVMethod]] = {}
        for method in lvclass.methods:
            if method.property_name is not None:
                groups.setdefault((method.property_name, method.scope), []).append(
                    method
                )
        fields = lvclass.private_data_fields
        taken = set(private_attribute_names(f.name for f in fields))
        accessors: list[PropertyAccessor] = []
        for (property_name, scope), members in groups.items():
            if self._scope_prefix(scope) + to_var_name(property_name) in taken:
                # The property would shadow a private data field's attribute.
                continue
            resolved = [
                r
                for m in members
                if (r := self._resolve_accessor(m, lvclass.name)) is not None
            ]
            roles = [r.role for r in resolved]
            if (
                len({r.attribute for r in resolved}) == 1
                and len(set(roles)) == len(roles)
            ):
                accessors.extend(resolved)
        return accessors

    def _resolve_accessor(
        self, method: LVMethod, class_name: str
    ) -> PropertyAccessor | None:
        """What the property VI ``method`` does, or None when its connector pane
        or its diagram does not say."""
        vi_context = self._method_contexts.get(method.name)
        if vi_context is None or self._graph is None:
            return None
        role = property_role(vi_context.inputs, vi_context.outputs, class_name)
        if role is None:
            return None
        attribute = accessed_attribute(vi_context, self._graph, class_name, role)
        if attribute is None:
            return None
        return PropertyAccessor(method, role, attribute)

    def _build_properties(
        self,
        accessors: list[PropertyAccessor],
    ) -> list[ast.stmt]:
        """Build @property and @setter from the accessors of each property: the
        getter returns, and the setter assigns, the private attribute of the
        field the accessor VI moves. A property is named by its long name, behind
        the prefix its scope gives a method."""
        property_stmts: list[ast.stmt] = []

        by_property: dict[tuple[str, str], dict[PropertyRole, PropertyAccessor]] = {}
        for acc in accessors:
            key = (acc.method.property_name or "", acc.method.scope)
            by_property.setdefault(key, {})[acc.role] = acc

        for (property_name, scope), acc_dict in by_property.items():
            getter = acc_dict.get(PropertyRole.GETTER)
            setter = acc_dict.get(PropertyRole.SETTER)
            anchor = getter or setter
            assert anchor is not None
            backing_field = anchor.attribute
            prop_name = self._scope_prefix(scope) + to_var_name(property_name)

            # Build getter
            if getter:
                getter_body: list[ast.stmt] = [
                    ast.Return(
                        value=ast.Attribute(
                            value=ast.Name(id="self", ctx=ast.Load()),
                            attr=backing_field,
                            ctx=ast.Load(),
                        )
                    )
                ]

                getter_def = ast.FunctionDef(
                    name=prop_name,
                    args=ast.arguments(
                        posonlyargs=[],
                        args=[ast.arg(arg="self", annotation=None)],
                        vararg=None,
                        kwonlyargs=[],
                        kw_defaults=[],
                        kwarg=None,
                        defaults=[],
                    ),
                    body=getter_body,
                    decorator_list=[ast.Name(id="property", ctx=ast.Load())],
                    returns=ast.Name(id="Any", ctx=ast.Load()),
                )
                property_stmts.append(getter_def)

            # Build setter
            if setter:
                setter_body: list[ast.stmt] = [
                    ast.Assign(
                        targets=[
                            ast.Attribute(
                                value=ast.Name(id="self", ctx=ast.Load()),
                                attr=backing_field,
                                ctx=ast.Store(),
                            )
                        ],
                        value=ast.Name(id="value", ctx=ast.Load()),
                    )
                ]

                setter_def = ast.FunctionDef(
                    name=prop_name,
                    args=ast.arguments(
                        posonlyargs=[],
                        args=[
                            ast.arg(arg="self", annotation=None),
                            ast.arg(
                                arg="value",
                                annotation=ast.Name(id="Any", ctx=ast.Load()),
                            ),
                        ],
                        vararg=None,
                        kwonlyargs=[],
                        kw_defaults=[],
                        kwarg=None,
                        defaults=[],
                    ),
                    body=setter_body,
                    decorator_list=[
                        ast.Attribute(
                            value=ast.Name(id=prop_name, ctx=ast.Load()),
                            attr="setter",
                            ctx=ast.Load(),
                        )
                    ],
                    returns=ast.Constant(value=None),
                )
                property_stmts.append(setter_def)

        return property_stmts
