"""The modules lvkit writes into every generated package hold no gap.

A ``raise NotImplementedError("<fixed text>")`` is a gap by the one gap rule: it is
warned about when the code is generated and names the VI and node it stands for.
The shared modules (``errors``, ``variants``, ``live_io``, ``panels``, ``scan``,
``notifiers``, ``grpc_server``) belong to no VI, so a refusal in them is a check of
an unusual value met at run time and names that value: its message is built when
it runs. A fixed-text raise there would also mark every module that imports the
shared one as holding a gap.
"""

from __future__ import annotations

import ast

import pytest

from lvkit.codegen.errors_module import ERRORS_MODULE_SOURCE
from lvkit.codegen.grpc_server_module import GRPC_SERVER_MODULE_SOURCE
from lvkit.codegen.live_io_module import LIVE_IO_MODULE_SOURCE
from lvkit.codegen.notifiers_module import NOTIFIERS_MODULE_SOURCE
from lvkit.codegen.panels_module import PANELS_MODULE_SOURCE
from lvkit.codegen.scan_module import SCAN_MODULE_SOURCE
from lvkit.codegen.variants_module import VARIANTS_MODULE_SOURCE

SOURCES = {
    "errors": ERRORS_MODULE_SOURCE,
    "grpc_server": GRPC_SERVER_MODULE_SOURCE,
    "live_io": LIVE_IO_MODULE_SOURCE,
    "notifiers": NOTIFIERS_MODULE_SOURCE,
    "panels": PANELS_MODULE_SOURCE,
    "scan": SCAN_MODULE_SOURCE,
    "variants": VARIANTS_MODULE_SOURCE,
}


def _fixed_text_raises(source: str) -> list[int]:
    """Lines of ``raise NotImplementedError("<a string constant>")``."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
            continue
        func = node.exc.func
        args = node.exc.args
        if (
            isinstance(func, ast.Name)
            and func.id == "NotImplementedError"
            and args
            and isinstance(args[0], ast.Constant)
            and isinstance(args[0].value, str)
        ):
            lines.append(node.lineno)
    return lines


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_a_shared_module_refuses_only_with_a_message_built_at_run_time(
    name: str,
) -> None:
    assert _fixed_text_raises(SOURCES[name]) == []
