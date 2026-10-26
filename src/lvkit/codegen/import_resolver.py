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


@dataclass(frozen=True)
class NameImport:
    """An import of a generated class or function: the statement (empty when
    the module defines the name itself) and the name the module spells it as.
    Two imports of one module that derive the same name are told apart by an
    ``as`` alias."""

    statement: str
    name: str


class ImportResolver:
    """Resolves how a generated module refers to another generated module.

    ``function`` gives the import of a VI's function (a subVI call). ``module``
    gives the statement importing the VI's whole module (a Global VI, whose controls are
    the module's attributes), plus the name the module is bound to.
    ``type_import`` gives the statement importing the class generated for a
    LabVIEW class or typedef a VI names; ``node_import`` the one importing the
    class generated for a known class or typedef node. ``error_import`` gives
    the statement importing ``LabVIEWError`` from the package's ``errors``
    module.
    """

    def __init__(
        self,
        function_import: Callable[[str, str | None], NameImport],
        module_import: Callable[[str], ModuleImport],
        type_import: Callable[[str], NameImport | None],
        node_import: Callable[[str], NameImport],
        error_import: Callable[[], str],
    ) -> None:
        self._function_import = function_import
        self._module_import = module_import
        self._type_import = type_import
        self._node_import = node_import
        self._error_import = error_import

    def function(self, vi_name: str, callee_key: str | None = None) -> NameImport:
        """The import of the VI's function and the name the module calls it by;
        an empty statement when the VI is the module being generated (a VI that
        calls itself defines the function it calls). The VI is identified by
        ``callee_key`` (its graph key); ``vi_name`` finds it only when there is
        none."""
        return self._function_import(vi_name, callee_key)

    def module(self, vi_name: str) -> ModuleImport:
        return self._module_import(vi_name)

    def type_import(self, type_ref: str) -> NameImport | None:
        """The statement importing the class of the LabVIEW class or typedef
        named ``type_ref``; None when the calling VI has no loaded dependency of
        that name."""
        return self._type_import(type_ref)

    def node_import(self, key: str) -> NameImport:
        """The statement importing the class generated for the class or typedef
        loaded at dependency key ``key``."""
        return self._node_import(key)

    def error_import(self) -> str:
        """The statement importing ``LabVIEWError`` from the package root."""
        return self._error_import()
