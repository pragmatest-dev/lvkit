"""The module generated for a VI that is not available.

A VI that a caller names but that was not found (in the project or its search
paths) has no diagram to convert. Its module defines the function the callers
import; calling it raises ``NotImplementedError``. It is a gap like any other.
"""

from __future__ import annotations

import ast

from lvkit.vi_files import strip_vi_extension

from .ast_utils import to_function_name
from .gaps import UNSUPPORTED_VI, Gap, raise_statement


def stub_module(vi_name: str) -> tuple[str, Gap]:
    """The module for the unavailable VI ``vi_name``, and its gap."""
    reason = (
        f"The VI {strip_vi_extension(vi_name)!r} was not found, so no code "
        "was generated for it"
    )
    function = ast.parse(
        f"def {to_function_name(vi_name)}(*args: Any, **kwargs: Any) -> Any: ..."
    ).body[0]
    assert isinstance(function, ast.FunctionDef)
    function.body = [raise_statement(reason)]
    module = ast.parse(
        f'"""Stub: {vi_name}."""\n'
        "from __future__ import annotations\n"
        "from typing import Any\n"
    )
    module.body.append(function)
    ast.fix_missing_locations(module)
    gap = Gap(
        vi=vi_name,
        node_id=None,
        node_name=None,
        kind=UNSUPPORTED_VI,
        identifier=vi_name,
        name=reason,
        reason=reason,
        detail=reason,
    )
    return ast.unparse(module) + "\n", gap
