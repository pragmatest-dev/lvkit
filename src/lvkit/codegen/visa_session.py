"""A VISA function given no session reports NI's invalid-session error.

A VISA session is a pyvisa resource object, so a session that was never made
(LabVIEW's Not a Refnum, Python's ``None``) has no method to call and the call
would raise ``AttributeError``, which no VISA failure handler catches. NI's VISA
error list gives that case its own error: -1073807346 "The given session or
object reference is invalid." (https://docs-be.ni.com/api/bundle/labview-api-ref/
page/errors/visa-error-codes.html; pyvisa's ``StatusCode.error_invalid_object``,
NI-VISA's ``VI_ERROR_INV_OBJECT``, 0xBFFF000E).

Every catalog node whose failures include pyvisa's ``VisaIOError`` and whose
input terminal is a VISA refnum (by the graph's type) first raises that error
for a session that is ``None``; the node's own handler turns it into its error
out like any other VISA failure, so error-in precedence is the node's own
(``native_failure.catch_failures``).
"""

from __future__ import annotations

import ast

from lvkit.graph.models import AnyGraphNode
from lvkit.lv_type_codes import REFNUM_TYPE
from lvkit.models import TerminalDirection
from lvkit.primitive_resolver import NativeError

from .context import CodeGenContext

VISA_REFNUM = REFNUM_TYPE.VisaRef.name
# NI, VISA Error Codes: -1073807346 "The given session or object reference is
# invalid."
INVALID_SESSION_CODE = -1073807346


def is_visa_failure(failure: NativeError) -> bool:
    """Whether ``failure`` is pyvisa's ``VisaIOError``."""
    return failure.exception == "VisaIOError" and failure.module == "pyvisa.errors"


def invalid_session_guards(
    node: AnyGraphNode, ctx: CodeGenContext, exception: str
) -> list[ast.stmt]:
    """For each wired VISA refnum input of ``node``,
    ``if <session> is None: raise <exception>(-1073807346)``, with ``exception``
    the name the module writes pyvisa's ``VisaIOError`` under."""
    statements: list[ast.stmt] = []
    for term in getattr(node, "terminals", ()):
        if (
            term.direction != TerminalDirection.INPUT
            or term.lv_type is None
            or term.lv_type.underlying_type != "Refnum"
            or term.lv_type.ref_type != VISA_REFNUM
        ):
            continue
        session = ctx.wired_input(term.id, node)
        if session is None:
            continue
        [guard] = ast.parse(
            f"if {session} is None:\n    raise {exception}({INVALID_SESSION_CODE})"
        ).body
        statements.append(guard)
    return statements
