"""The module-level ``ctypes`` code of a generated module: the libraries it loads,
the function prototypes it sets and the ``ctypes.Structure`` classes it defines.

Each declaration is registered once under a key and emitted in the order it was
first needed, so a declaration always follows the ones it refers to.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field


@dataclass
class CtypesModule:
    """Declarations of one generated module, in definition order."""

    statements: list[ast.stmt] = field(default_factory=list)
    _declared: dict[Hashable, str] = field(default_factory=dict)
    _taken: set[str] = field(default_factory=set)

    def declare(
        self, key: Hashable, preferred: str, source: Callable[[str], str]
    ) -> str:
        """The name ``key`` is declared under, declaring it first when it is new.

        ``source`` receives the name (``preferred``, made distinct from every
        name already declared) and returns the declaration's Python source. It
        may declare the declarations it refers to before returning."""
        if key in self._declared:
            return self._declared[key]
        name, number = preferred, 1
        while name in self._taken:
            number += 1
            name = f"{preferred}{number}"
        self._taken.add(name)
        text = source(name)
        self._declared[key] = name
        self.statements.extend(ast.parse(text).body)
        return name
