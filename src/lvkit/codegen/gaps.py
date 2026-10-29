"""Gaps: the parts of a VI that code generation cannot convert.

Every gap goes through one mechanism, wherever it is found. Generation records a
``Gap`` (VI, node, kind, reason) that the caller prints as a warning, and the
generated code raises ``NotImplementedError(<reason>)`` where the node would run,
with the node's outputs bound to ``None``. Only ``ConversionGap`` errors take this
path; errors that mean the package cannot be written, or that lvkit itself is
wrong, stop generation.
"""

from __future__ import annotations

import ast
import dataclasses
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import AnyGraphNode, StructureNode
from lvkit.models import Terminal, TunnelTerminal
from lvkit.parser.node_types import get_display_name
from lvkit.primitive_resolver import PrimitiveResolutionNeeded, TerminalResolutionNeeded
from lvkit.vilib_resolver import VILibResolutionNeeded

from .ast_utils import build_assign, to_var_name
from .errors import MissingVIError, UnknownDefault
from .fragment import CodeFragment
from .module_declarations import ModuleDeclarations

if TYPE_CHECKING:
    from .context import CodeGenContext

# The kinds `lvkit unresolved` lists. A gap fits the first four by what it is;
# every other node gap is an "unsupported_node", and a gap in no node at all (a
# class whose parent is not loaded, a constant with no value to write) is an
# "unsupported_vi". A call of a VI that was not found is a "missing_vi", and an
# unwired input the function's code uses, whose default the catalog does not
# know, is an "unknown_default".
UNKNOWN_PRIMITIVE = "unknown_primitive"
PLACEHOLDER_PRIMITIVE = "placeholder_primitive"
UNSUPPORTED_NODE = "unsupported_node"
UNMAPPED_VILIB = "unmapped_vilib"
TERMINAL_MAPPING = "terminal_mapping"
UNKNOWN_DEFAULT = "unknown_default"
UNSUPPORTED_VI = "unsupported_vi"
MISSING_VI = "missing_vi"


@dataclass(frozen=True)
class Gap:
    """One thing in a VI that code generation cannot convert."""

    vi: str
    node_id: str | None
    node_name: str | None
    kind: str
    # What the gap is about: a primResID, a node kind, a VI name.
    identifier: str
    name: str
    reason: str
    # The full diagnostic the error carries.
    detail: str

    def diagnostic(self) -> list[str]:
        """The lines of the error's own diagnostic that say more than the reason
        (the primitive's connector pane, the fix to make)."""
        if not self.detail or " ".join(self.detail.split()) == self.reason:
            return []
        return [
            line
            for line in self.detail.splitlines()
            if line.strip() and not line.strip().startswith("In VI:")
        ]

    @property
    def message(self) -> str:
        """What the generated ``NotImplementedError`` says: the reason, then the
        kind, VI and node, then the diagnostic the error carries (the primitive's
        connector pane, the paths tried), enough to finish the conversion without
        running lvkit again."""
        lines = [self.reason, f"gap kind: {self.kind}", f"VI: {Path(self.vi).name}"]
        if self.node_name:
            lines.append(f"node: {self.node_name}")
        lines.extend(self.diagnostic())
        return "\n".join(lines)


class GapStop(BaseException):
    """A fail-fast run reached its first gap. Not an ``Exception``, so no handler
    that keeps a run going after a stopped VI catches it."""

    def __init__(self, gap: Gap) -> None:
        self.gap = gap
        super().__init__(gap.reason)


class GapLog(list[Gap]):
    """The gaps of a run, in the order found. Recording a gap is the one place a
    fail-fast run ends: it raises ``GapStop`` right after the gap is added."""

    def __init__(self, fail_fast: bool = False) -> None:
        super().__init__()
        self.fail_fast = fail_fast

    def append(self, gap: Gap) -> None:
        super().append(gap)
        if self.fail_fast:
            raise GapStop(gap)


def gap_of(
    exc: ConversionGap,
    vi: str,
    node: AnyGraphNode | None,
    label: str | None = None,
) -> Gap:
    """The record of ``exc``, raised while generating ``node`` of ``vi``; ``label``
    names what the gap is in when it is in no node (a constant)."""
    node = node or exc.node
    node_name = node.display_name if node is not None else label or exc.node_name
    reason = exc.summary()
    kind, identifier, name = UNSUPPORTED_VI, vi, reason
    if isinstance(exc, PrimitiveResolutionNeeded):
        if exc.node_kind:
            kind, identifier = UNSUPPORTED_NODE, exc.node_kind
            name = exc.prim_name or "unknown"
        else:
            kind = PLACEHOLDER_PRIMITIVE if exc.placeholder else UNKNOWN_PRIMITIVE
            identifier, name = exc.prim_id, exc.prim_name or "unknown"
    elif isinstance(exc, VILibResolutionNeeded):
        kind, identifier, name = UNMAPPED_VILIB, exc.vi_name, exc.vi_name
    elif isinstance(exc, MissingVIError):
        kind, identifier, name = MISSING_VI, exc.name, exc.name
    elif isinstance(exc, TerminalResolutionNeeded):
        kind, identifier, name = TERMINAL_MAPPING, exc.prim_name, exc.prim_name
    elif isinstance(exc, UnknownDefault):
        kind, identifier, name = UNKNOWN_DEFAULT, exc.function, exc.terminal
    elif node is not None:
        kind = UNSUPPORTED_NODE
        identifier = get_display_name(node.node_type) if node.node_type else "node"
    return Gap(
        vi=vi,
        node_id=node.id if node is not None else None,
        node_name=node_name,
        kind=kind,
        identifier=identifier,
        name=name,
        reason=reason,
        detail=str(exc),
    )


def raise_statement(reason: str) -> ast.stmt:
    """``raise NotImplementedError(reason)``."""
    return ast.Raise(
        exc=ast.Call(
            func=ast.Name(id="NotImplementedError", ctx=ast.Load()),
            args=[ast.Constant(value=reason)],
            keywords=[],
        ),
        cause=None,
    )


def is_gap_raise(stmt: ast.stmt) -> bool:
    """Whether ``stmt`` is a ``raise NotImplementedError(...)``: the raise a gap
    writes. The code after it is kept, for the developer who replaces the raise."""
    return (
        isinstance(stmt, ast.Raise)
        and isinstance(stmt.exc, ast.Call)
        and isinstance(stmt.exc.func, ast.Name)
        and stmt.exc.func.id == "NotImplementedError"
    )


def _outputs(node: AnyGraphNode) -> list[Terminal]:
    """The terminals other nodes read from ``node``: its outputs, and for a
    structure the outer side of its output tunnels (its other terminals belong to
    its diagram). An error cluster carries nothing (an error is an exception)."""
    if isinstance(node, StructureNode):
        return [
            term
            for term in node.terminals
            if isinstance(term, TunnelTerminal)
            and term.boundary == "outer"
            and term.direction == "output"
            and not term.is_error_cluster
        ]
    return [
        term
        for term in node.terminals
        if term.direction == "output" and not term.is_error_cluster
    ]


def gap_fragment(node: AnyGraphNode, ctx: CodeGenContext, reason: str) -> CodeFragment:
    """What a gap node generates: its outputs bound to ``None``, then the
    ``raise``."""
    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    for term in _outputs(node):
        name = (
            ctx.output_tunnel_var_name(term.id, node.id)
            if isinstance(term, TunnelTerminal)
            else None
        )
        var = ctx.make_output_var(
            to_var_name(name or term.name or f"out_{term.index}"),
            node.id,
            terminal_id=term.id,
        )
        ctx.bind(term.id, var)
        bindings[term.id] = var
        statements.append(build_assign(var, ast.Constant(value=None)))
    statements.append(raise_statement(reason))
    return CodeFragment(statements=statements, bindings=bindings)


class ModuleState:
    """Every store a generator adds to as it writes a module (imports, helper
    functions, ``ctypes`` declarations, data files, the gap list), copied so
    the additions of a generator that fails can be taken back."""

    def __init__(self, ctx: CodeGenContext) -> None:
        self._ctx = ctx
        self._saved: dict[str, Any] = {}
        for field in dataclasses.fields(ctx):
            value = getattr(ctx, field.name)
            if isinstance(value, ModuleDeclarations):
                self._saved[field.name] = value.snapshot()
            elif isinstance(value, (set, dict, list)):
                self._saved[field.name] = (
                    set(value)
                    if isinstance(value, set)
                    else dict(value)
                    if isinstance(value, dict)
                    else list(value)
                )

    def restore(self) -> None:
        """Put every store back to what it held when this state was taken."""
        for name, saved in self._saved.items():
            current = getattr(self._ctx, name)
            if isinstance(current, ModuleDeclarations):
                current.restore(saved)
            elif isinstance(current, set):
                current.clear()
                current.update(saved)
            elif isinstance(current, dict):
                current.clear()
                current.update(saved)
            else:
                current[:] = saved


def _wired_terminals(node: AnyGraphNode, ctx: CodeGenContext) -> list[str]:
    """The wired terminals of a call, as ``input index=0 type=NumInt32``."""
    return [
        f"{t.direction} index={t.index} type="
        f"{(t.lv_type.underlying_type or t.lv_type.kind.value) if t.lv_type else '?'}"
        for t in node.terminals
        if ctx.is_wired(t.id)
    ]


class GapGuard:
    """The outcome of ``catch_gap``: the fragment of the gap, when one occurred."""

    def __init__(self) -> None:
        self.fragment: CodeFragment | None = None


@contextmanager
def catch_gap(node: AnyGraphNode, ctx: CodeGenContext) -> Iterator[GapGuard]:
    """Run a generator of ``node``; when it fails with a ``ConversionGap``, take
    back what it added to the module, record the gap, and leave in
    ``guard.fragment`` the code that raises where the node would run."""
    guard = GapGuard()
    state = ModuleState(ctx)
    try:
        yield guard
    except ConversionGap as exc:
        state.restore()
        if isinstance(exc, MissingVIError):
            exc.call_terminals = _wired_terminals(node, ctx)
        gap = gap_of(exc, ctx.vi_name or "", node)
        ctx.gaps.append(gap)
        guard.fragment = gap_fragment(node, ctx, gap.message)


class EntryGuard:
    """The outcome of ``catch_entry_gap``."""

    def __init__(self) -> None:
        self.failed = False


@contextmanager
def catch_entry_gap(ctx: CodeGenContext, label: str | None) -> Iterator[EntryGuard]:
    """Run code that makes a value the function evaluates when it is entered (a
    constant). When it fails with a ``ConversionGap``, take back what it added to
    the module, record the gap, and add the ``raise`` to the start of the
    function: the value is evaluated there."""
    guard = EntryGuard()
    state = ModuleState(ctx)
    try:
        yield guard
    except ConversionGap as exc:
        state.restore()
        gap = gap_of(exc, ctx.vi_name or "", None, label)
        ctx.gaps.append(gap)
        ctx.entry_raises.append(raise_statement(gap.message))
        guard.failed = True


def vi_body(
    ctx: CodeGenContext, vi_name: str, build: Callable[[], list[ast.stmt]]
) -> list[ast.stmt]:
    """The statements ``build`` makes for the function of VI ``vi_name``. A gap
    that belongs to no node makes the whole body raise, after the raises of what
    the function evaluates when it is entered."""
    state = ModuleState(ctx)
    try:
        return build()
    except ConversionGap as exc:
        state.restore()
        gap = gap_of(exc, vi_name, None)
        ctx.gaps.append(gap)
        return [*ctx.entry_raises, raise_statement(gap.message)]
