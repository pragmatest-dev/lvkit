"""The import statements a generated module needs for other generated modules."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lvkit.graph.models import AnyGraphNode

from .errors import CodeGenError

if TYPE_CHECKING:
    from .package_modules import PackageModules
    from .queue_module import QueueUse
    from .shared_variable_module import SharedVariableAttributes


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
        package_spec: Callable[[str], str],
        package_modules: PackageModules | None = None,
        root_module_import: Callable[[str, str], ModuleImport] | None = None,
    ) -> None:
        self._function_import = function_import
        self._module_import = module_import
        self._type_import = type_import
        self._node_import = node_import
        self._error_import = error_import
        self._package_spec = package_spec
        self._package_modules = package_modules
        self._root_module_import = root_module_import

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
    @property
    def has_package_modules(self) -> bool:
        """Whether this resolver belongs to a generated package that defines
        shared queues and Shared Variables."""
        return self._package_modules is not None

    def _modules(
        self, what: str, node: AnyGraphNode | None
    ) -> tuple[PackageModules, Callable[[str, str], ModuleImport]]:
        if self._package_modules is None or self._root_module_import is None:
            raise CodeGenError(
                f"{what} is shared by name, and there is no generated package to "
                "define it in",
                node,
            )
        return self._package_modules, self._root_module_import

    def queue_use(self, node: AnyGraphNode) -> tuple[QueueUse, ModuleImport]:
        """How the Obtain Queue ``node`` names its queue, from the package's
        queries, and the import of the package's ``queues`` module."""
        modules, root_import = self._modules("A queue", node)
        use = modules.queues.use_of(node.id, node)
        if use.error is not None:
            raise CodeGenError(use.error, node)
        return use, root_import(modules.queues_stem, "shared_queues")

    def shared_variable(
        self, node: AnyGraphNode
    ) -> tuple[ModuleImport, SharedVariableAttributes]:
        """The import of the package's ``shared_variables`` module and the
        attributes of the Shared Variable ``node`` in it."""
        modules, root_import = self._modules(f"Shared Variable '{node.name}'", node)
        attributes = modules.shared_variables.by_node.get(node.id)
        if attributes is None:
            raise CodeGenError(
                "This Shared Variable was not among the package's nodes, so its "
                "module cannot be connected",
                node,
            )
        if attributes.error is not None:
            raise CodeGenError(attributes.error, node)
        return (
            root_import(modules.shared_variables_stem, "shared_variables_module"),
            attributes,
        )
    def package_spec(self, key: str) -> str:
        """The start of the relative import spec of a module in the folder holding
        the file ``key``: the dots, then each package and a dot. A module found by
        a name computed at run time is imported with this, the module's name and
        ``__package__``."""
        return self._package_spec(key)
