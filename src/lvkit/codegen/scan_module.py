"""The module a generated package holds for a Scan From String whose format string
is known only when the code runs.

A format string written on the diagram is turned into one ``re`` pattern while
generating (``labview_scan.scan_plan``). A format string wired from a value the
code computes needs the same translation at run time, so the package gets
``scan.py``: the source of that translator, copied from the modules the generator
imports (``labview_scan``, ``labview_scan_time``, ``labview_format``'s
``_lv_parse``, ``_lv_decode_escapes`` and ``_lv_default_code``, and
``FormatKind``), so the pattern and
the conversion of each output are the ones a constant format gets and the two
cannot differ. Nothing is re-typed: the module's body is the bodies of those
modules (``ast.unparse``) with their imports replaced by the header below.

The run-time entry is ``scan_text``: it translates the format string once for
each (format string, kinds of the outputs) pair, matches the text, and returns a
``ScanResult`` with the same ``end()`` and ``lastindex`` a constant format's
``re.match`` result has and ``value(position, default)`` for each output. A format
string with no conversion (a specifier or output type the generator would call a
gap) raises ``NotImplementedError`` naming the format string and the reason.

It is written once into the package root as ``scan.py``, only when a generated
module imports it, and imports nothing from the package or from lvkit.
"""

from __future__ import annotations

import ast
import inspect
import re
from types import ModuleType

from . import labview_scan, labview_scan_time
from .labview_format import (
    FormatKind,
    _lv_decode_escapes,
    _lv_default_code,
    _lv_parse,
    helper_sources,
)

SCAN_MODULE_NAME = "scan"
SCAN_FUNCTION_NAME = "scan_text"

# A relative import of the package's ``scan`` module.
_SCAN_IMPORT = re.compile(rf"^from \.+{SCAN_MODULE_NAME} import ")


def imports_scan(statement: str) -> bool:
    """Whether ``statement`` imports from the package's ``scan`` module."""
    return _SCAN_IMPORT.match(statement) is not None


def scan_import(prefix: str) -> str:
    """``from <prefix>scan import scan_text``."""
    return f"from {prefix}{SCAN_MODULE_NAME} import {SCAN_FUNCTION_NAME}"


_HEADER = '''"""The translator of a Scan From String format string, for one the code
computes: the same that turns a format string written on the diagram into a pattern
when the code is generated."""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import NamedTuple
'''

_RUNTIME = '''

@functools.lru_cache(maxsize=None)
def _compiled(format_text, kinds):
    """The compiled pattern and the converter of each output for ``format_text``
    read into outputs of ``kinds`` (a tuple of ``FormatKind`` fields)."""
    pieces = scan_pieces(format_text)
    if field_count(pieces) != len(kinds):
        raise NotImplementedError(
            f"The format string {format_text!r} of Scan From String has "
            f"{field_count(pieces)} specifiers and the node has {len(kinds)} outputs"
        )
    try:
        plan = scan_plan(pieces, [FormatKind(*kind) for kind in kinds])
    except ValueError as reason:
        raise NotImplementedError(f"{reason} (format string {format_text!r})") from None
    namespace = {"re": re}
    for statement in plan.imports:
        exec(statement, namespace)
    converters = [
        eval("lambda g, d: " + value.format(g="g", d="d"), namespace)
        for value in plan.values
    ]
    return re.compile(plan.pattern), converters


class ScanResult:
    """What a scan read: where it ended, how many specifiers it reached, and the
    value of each output."""

    def __init__(self, match, converters):
        self._match = match
        self._converters = converters
        self.lastindex = match.lastindex

    def end(self):
        return self._match.end()

    def value(self, position, default):
        """The value of output ``position``: its text converted to the output's
        type, or ``default`` when the scan stopped before it."""
        group = self._match[position + 1]
        if group is None:
            return default
        return self._converters[position](group, default)


def scan_text(format_text, text, kinds):
    """Scan ``text`` by ``format_text`` into outputs of ``kinds``, a tuple with one
    ``(kind, bits, signed, labels)`` for each output."""
    pattern, converters = _compiled(format_text, kinds)
    return ScanResult(pattern.match(text), converters)
'''


def _body(module: ModuleType) -> str:
    """The source of ``module`` without its docstring and imports."""
    tree = ast.parse(inspect.getsource(module))
    kept = [
        node
        for node in tree.body
        if not (
            isinstance(node, ast.Import | ast.ImportFrom | ast.If)
            or (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            )
        )
    ]
    return "\n\n\n".join(ast.unparse(node) for node in kept)


def _module_source() -> str:
    parts = [
        _HEADER,
        inspect.getsource(FormatKind),
        *helper_sources(_lv_parse, _lv_decode_escapes, _lv_default_code),
        _body(labview_scan_time),
        _body(labview_scan),
    ]
    return "\n\n".join(parts) + "\n" + _RUNTIME


SCAN_MODULE_SOURCE = _module_source()
