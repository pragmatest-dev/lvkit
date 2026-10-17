"""The import statements a generated module needs for other generated VIs."""

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
    """Resolves how a generated module refers to another generated VI.

    Calling it with a VI name gives the statement importing that VI's function
    (a subVI call). ``module`` gives the statement importing the VI's whole
    module (a Global VI, whose controls are the module's attributes), plus the
    name the module is bound to.
    """

    def __init__(
        self,
        function_import: Callable[[str], str],
        module_import: Callable[[str], ModuleImport],
    ) -> None:
        self._function_import = function_import
        self._module_import = module_import

    def __call__(self, vi_name: str) -> str:
        return self._function_import(vi_name)

    def module(self, vi_name: str) -> ModuleImport:
        return self._module_import(vi_name)
