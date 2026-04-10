"""Import statement builder.

Collects and generates Python import statements.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..context import VISignature


@dataclass
class Import:
    """A single import."""
    module: str
    names: set[str] = field(default_factory=set)
    is_from: bool = True  # from X import Y vs import X


class ImportBuilder:
    """Builds import statements for generated code.

    Collects imports as code is generated, then produces
    sorted, deduplicated import statements.
    """

    def __init__(self):
        self._imports: dict[str, Import] = {}
        self._future_imports: set[str] = {"annotations"}

    def add_future(self, name: str) -> None:
        """Add a __future__ import."""
        self._future_imports.add(name)

    def add_from(self, module: str, *names: str) -> None:
        """Add a from X import Y statement.

        Args:
            module: The module to import from
            names: Names to import from the module
        """
        if module not in self._imports:
            self._imports[module] = Import(module=module, is_from=True)
        self._imports[module].names.update(names)

    def add_import(self, module: str) -> None:
        """Add a plain import X statement."""
        if module not in self._imports:
            self._imports[module] = Import(module=module, is_from=False)

    def add_pathlib(self) -> None:
        """Add pathlib.Path import."""
        self.add_from("pathlib", "Path")

    def add_typing(self, *names: str) -> None:
        """Add typing imports."""
        self.add_from("typing", *names)

    def add_namedtuple(self) -> None:
        """Add NamedTuple import."""
        self.add_typing("NamedTuple")

    def add_dependency(self, sig: VISignature) -> None:
        """Add imports for a converted dependency.

        Args:
            sig: The VISignature of the dependency
        """
        # Import the function and result type
        module = f".{sig.function_name}"
        names = [sig.function_name]
        result_type = getattr(sig, "result_type", None)
        if result_type:
            names.append(result_type)
        # Add any enum types
        for enum_name in getattr(sig, "enum_types", []):
            names.append(enum_name)

        self.add_from(module, *names)

    def add_vilib(self, module_name: str, *names: str) -> None:
        """Add vilib imports.

        Args:
            module_name: The vilib module (e.g., "get_system_directory")
            names: Names to import (function, result type, enums)
        """
        self.add_from(f".{module_name}", *names)

    def generate(self) -> list[str]:
        """Generate sorted import statements.

        Returns:
            List of import statement strings
        """
        lines = []

        # __future__ imports first
        if self._future_imports:
            future_names = ", ".join(sorted(self._future_imports))
            lines.append(f"from __future__ import {future_names}")
            lines.append("")

        # Standard library imports
        stdlib = []
        local = []

        for module, imp in sorted(self._imports.items()):
            if module.startswith("."):
                local.append(imp)
            else:
                stdlib.append(imp)

        # Stdlib imports
        for imp in stdlib:
            if imp.is_from and imp.names:
                names = ", ".join(sorted(imp.names))
                lines.append(f"from {imp.module} import {names}")
            elif not imp.is_from:
                lines.append(f"import {imp.module}")

        if stdlib and local:
            lines.append("")

        # Local imports
        for imp in local:
            if imp.is_from and imp.names:
                names = ", ".join(sorted(imp.names))
                lines.append(f"from {imp.module} import {names}")

        return lines
