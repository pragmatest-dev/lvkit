"""The import statements a generated module needs for other generated modules."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ModuleImport:
    """An import of a whole generated module: the statement and the name the
    module is bound to."""

    statement: str
    alias: str


class ImportResolver:
    """Resolves how a generated module refers to another generated module.

    Calling it with a VI name gives the statement importing that VI's function
    (a subVI call), or ``""`` when the VI is the module being generated: a VI
    that calls itself defines the function it calls. ``module`` gives the
    statement importing the VI's whole module (a Global VI, whose controls are
    the module's attributes), plus the name the module is bound to.
    ``type_import`` gives the statement importing the class generated for a
    LabVIEW class or typedef a VI names; ``node_import`` the one importing the
    class generated for a known class or typedef node.
    """

    def __init__(
        self,
        function_import: Callable[[str], str],
        module_import: Callable[[str], ModuleImport],
        type_import: Callable[[str], str | None],
        node_import: Callable[[str], str],
    ) -> None:
        self._function_import = function_import
        self._module_import = module_import
        self._type_import = type_import
        self._node_import = node_import

    def __call__(self, vi_name: str) -> str:
        return self._function_import(vi_name)

    def module(self, vi_name: str) -> ModuleImport:
        return self._module_import(vi_name)

    def type_import(self, type_ref: str) -> str | None:
        """The statement importing the class of the LabVIEW class or typedef
        named ``type_ref``; None when the calling VI has no loaded dependency of
        that name."""
        return self._type_import(type_ref)

    def node_import(self, key: str) -> str:
        """The statement importing the class generated for the class or typedef
        loaded at dependency key ``key``."""
        return self._node_import(key)
