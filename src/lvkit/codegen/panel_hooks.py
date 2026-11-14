"""What generated code says about the live values of a running VI's front panel.

A VI whose diagram reads or writes its own front panel while it runs (a control
or indicator terminal inside a loop or Event Structure, a Local Variable of one
of its controls or indicators, a Value Property Node bound to one of its
controls, or an Event Structure: ``InMemoryVIGraph.touches_panel_while_running``)
takes one keyword-only parameter after its connector-pane ones::

    live_io: LiveIO = NO_LIVE_IO

``LiveIO`` is the class of the ``live_io`` module the package holds
(``codegen/live_io_module.py``); its methods do nothing, so a caller that passes
nothing gets the VI exactly as it is without the parameter. The VI calls
``live_io.write(AcquireValue.COUNT, value)`` after every write of one of its
indicators, and of one of its controls by a Local Variable.
``live_io.read`` is called before a control that the diagram reads while running
is used: at the start of the diagram that holds a control terminal inside a loop
(``reads_in_frame``), at a Local Variable read of a control inside a loop (the
node generator of ``nodes/local_variable.py``), and once at entry for an
off-pane control the diagram reads (``entry_reads``)::

    stop = live_io.read(AcquireValue.STOP, stop)

A VI whose bound Property Nodes write a listed control property
(``control_properties.json``) calls
``live_io.set_property(AcquireValue.COUNT, AcquireProperty.VISIBLE, flag)`` at
each such write.

The name is a member of ``AcquireValue``, the ``(str, Enum)`` the module of the
VI defines before its function (``PanelHooks.for_vi`` writes it): one member per
control and indicator, the member's value the name string (``"count"``), so a
panel imports the enum with the function and its logic and the panel share one
definition. The name is the control's parameter name or the indicator's result
field name, made distinct across the controls and indicators of the VI (a
control and an indicator may share a label). A cluster is passed whole. The
generated code imports nothing from lvkit.

A VI that holds an Event Structure takes the front panel's events from
``live_io.events`` (``event_kinds``, ``nodes/event.py``); ``PanelHooks.for_vi``
also writes the ``(str, Enum)`` of the kinds of event (``AcquireEvent``) before
its function.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lvkit.control_properties import ControlProperty
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import EventStructureNode, PrimitiveNode, VIContext
from lvkit.models import FPTerminal, Terminal

from .ast_utils import (
    EVENTS,
    LIVE_IO,
    build_assign,
    class_style_name,
    distinct_names,
    parse_expr,
    parse_stmt,
    unique_names,
)
from .errors import CodeGenError
from .event_kinds import (
    MESSAGE_KINDS,
    NAME,
    NEW_VALUE,
    QUEUE_IMPORT,
    VALUE_CHANGE_CODE,
    MessageKind,
)
from .live_io_module import LIVE_IO_NAME, NO_LIVE_IO_NAME
from .panel_names import panel_names

if TYPE_CHECKING:
    from .context import CodeGenContext


@dataclass(frozen=True)
class PanelHooks:
    """The hooks of one VI: ``enum_name`` is the name of the enum its module
    defines, ``names`` maps the terminal id of each control and indicator of its
    front panel to the name the hooks pass for it and ``members`` to the
    enum's member that holds the name, ``indicators`` lists the terminal ids of
    the indicators, pane first, and ``controls`` those of the controls.
    ``property_enum`` is the name of the enum of the control properties the VI
    writes (None when it writes none) and ``property_members`` maps the Python
    name of each (``ControlProperty.member``) to the enum's member.
    ``event_enum_name`` is the name of the enum of the kinds of event
    (``codegen/event_kinds.py``) a VI with an Event Structure defines and takes
    the front panel's events for, None for a VI without one."""

    enum_name: str
    names: dict[str, str]
    members: dict[str, str]
    indicators: tuple[str, ...]
    controls: tuple[str, ...]
    property_enum: str | None = None
    property_members: dict[str, str] = field(default_factory=dict)
    event_enum_name: str | None = None

    @classmethod
    def for_vi(
        cls,
        ctx: CodeGenContext,
        vi_context: VIContext,
        graph: InMemoryVIGraph | None,
        vi_name: str,
        enum_name: str,
        property_enum_name: str,
        taken: Iterable[str] = (),
        parameter_names: dict[str, str] | None = None,
    ) -> PanelHooks | None:
        """The hooks of ``vi_name``, None when its diagram does not touch its
        front panel while it runs. The enum is added to the module of ``ctx``
        under ``enum_name``, or a name made distinct from ``taken`` (the other
        names the module defines) and the names it already holds; so is the
        enum of the control properties the VI writes, under ``property_enum_name``,
        when it writes any (``InMemoryVIGraph.listed_property_writes``).
        ``parameter_names`` (terminal id to name) are the names a caller gave
        the parameters, when they are not the connector pane's own."""
        if graph is None or not graph.touches_panel_while_running(vi_name):
            return None
        names = panel_names(vi_context, parameter_names)
        named_controls = tuple(
            c.id
            for c in [*vi_context.inputs, *vi_context.off_pane_inputs]
            if c.id in names
        )
        member_names = unique_names(
            [name.upper().strip("_") or "NAME" for name in names.values()]
        )
        members = dict(zip(names, member_names, strict=True))
        ctx.import_live_io()
        written = ctx.add_module_function(
            _enum_def(
                distinct_names([*taken, *ctx.helper_names.values()], [enum_name])[0],
                [(members[t], names[t]) for t in names],
            )
        )
        written_properties = _written_properties(graph, vi_name)
        property_enum = None
        if written_properties:
            property_enum = ctx.add_module_function(
                _enum_def(
                    distinct_names(
                        [*taken, *ctx.helper_names.values()], [property_enum_name]
                    )[0],
                    [(p.member.upper(), p.member) for p in written_properties],
                )
            )
        has_events = graph.has_event_structure(vi_name)
        if has_events:
            ctx.imports.add(QUEUE_IMPORT)
        event_enum = (
            ctx.add_module_function(
                _enum_def(
                    distinct_names(
                        [*taken, *ctx.helper_names.values()], [_event_enum(written)]
                    )[0],
                    [(kind.member, kind.name) for kind in MESSAGE_KINDS],
                )
            )
            if has_events
            else None
        )
        return cls(
            written,
            names,
            members,
            tuple(t.id for t in [*vi_context.outputs, *vi_context.off_pane_outputs]),
            named_controls,
            property_enum,
            {p.member: p.member.upper() for p in written_properties},
            event_enum,
        )

    def member(self, terminal_id: str) -> ast.expr:
        """``<Enum>.<MEMBER>``, the name passed for the terminal."""
        return ast.Attribute(
            value=ast.Name(id=self.enum_name, ctx=ast.Load()),
            attr=self.members[terminal_id],
            ctx=ast.Load(),
        )

    def event_member(self, kind: MessageKind) -> str:
        """``<EventEnum>.<MEMBER>``, the kind of event ``kind`` as a message
        carries it."""
        return f"{self.event_enum_name}.{kind.member}"

    def property_member(self, prop: ControlProperty) -> ast.expr:
        """``<PropertyEnum>.<MEMBER>``, the property passed for ``prop``."""
        if self.property_enum is None:
            raise CodeGenError("The VI has no property enum", None)
        return ast.Attribute(
            value=ast.Name(id=self.property_enum, ctx=ast.Load()),
            attr=self.property_members[prop.member],
            ctx=ast.Load(),
        )

    def arguments(self) -> tuple[list[ast.arg], list[ast.expr]]:
        """The keyword-only parameter of the VI, and its default."""
        return (
            [
                ast.arg(
                    arg=LIVE_IO,
                    annotation=ast.Name(id=LIVE_IO_NAME, ctx=ast.Load()),
                )
            ],
            [ast.Name(id=NO_LIVE_IO_NAME, ctx=ast.Load())],
        )


def function_enum_name(vi_name: str) -> str:
    """The wanted name of the enum of a VI's function, its name in class style
    (``acquire`` is ``AcquireValue``)."""
    return f"{class_style_name(vi_name)}Value"


def _event_enum(value_enum_name: str) -> str:
    """The wanted name of the enum of the kinds of event of a VI, from the name of
    its enum of names (``AcquireValue`` is ``AcquireEvent``)."""
    return f"{value_enum_name.removesuffix('Value')}Event"


def method_enum_name(class_name: str, method_name: str) -> str:
    """The wanted name of the enum of a class method, the class, then the method
    (``ScopeAcquireValue``)."""
    return f"{class_name}{class_style_name(method_name)}Value"


def function_property_enum_name(vi_name: str) -> str:
    """The wanted name of the enum of the control properties a VI's function
    writes (``acquire`` is ``AcquireProperty``)."""
    return f"{class_style_name(vi_name)}Property"


def method_property_enum_name(class_name: str, method_name: str) -> str:
    """The wanted name of the enum of the control properties a class method
    writes (``ScopeAcquireProperty``)."""
    return f"{class_name}{class_style_name(method_name)}Property"


def _written_properties(graph: InMemoryVIGraph, vi_name: str) -> list[ControlProperty]:
    """The control properties the Property Nodes of ``vi_name`` write, each once,
    in diagram order."""
    found: dict[str, ControlProperty] = {}
    for node in graph.iter_nodes(vi_name):
        if isinstance(node, PrimitiveNode) and node.bound_control_uid:
            for _row, _term, prop in graph.listed_property_writes(node):
                found.setdefault(prop.member, prop)
    return list(found.values())


def _enum_def(name: str, members: list[tuple[str, str]]) -> ast.ClassDef:
    """``class <name>(str, Enum)`` with a member (name and value) for each of
    ``members``."""
    return ast.ClassDef(
        name=name,
        bases=[ast.Name(id="str", ctx=ast.Load()), ast.Name(id="Enum", ctx=ast.Load())],
        keywords=[],
        body=[
            ast.Assign(
                targets=[ast.Name(id=member, ctx=ast.Store())],
                value=ast.Constant(value=value),
            )
            for member, value in members
        ]
        or [ast.Pass()],
        decorator_list=[],
    )


def _live_io_call(method: str, args: list[ast.expr], target: str | None) -> ast.stmt:
    """``live_io.<method>(<args>)``, the result assigned to ``target`` when one
    is given."""
    call = ast.Call(
        func=ast.Attribute(
            value=ast.Name(id=LIVE_IO, ctx=ast.Load()), attr=method, ctx=ast.Load()
        ),
        args=args,
        keywords=[],
    )
    if target is None:
        return ast.Expr(value=call)
    return ast.Assign(targets=[ast.Name(id=target, ctx=ast.Store())], value=call)


def write_stmt(hooks: PanelHooks, terminal_id: str, variable: str) -> ast.stmt:
    """``live_io.write(<Enum>.<MEMBER>, <variable>)``, the member of the control
    or indicator ``terminal_id``."""
    args = [hooks.member(terminal_id), ast.Name(id=variable, ctx=ast.Load())]
    return _live_io_call("write", args, None)


def property_stmt(
    hooks: PanelHooks, terminal_id: str, prop: ControlProperty, value: str
) -> ast.stmt:
    """``live_io.set_property(<Enum>.<MEMBER>, <PropertyEnum>.<PROPERTY>,
    <value>)``, the property ``prop`` of the control or indicator ``terminal_id``
    set to ``value`` (an expression)."""
    args = [
        hooks.member(terminal_id),
        hooks.property_member(prop),
        parse_expr(value),
    ]
    return _live_io_call("set_property", args, None)


def read_stmt(hooks: PanelHooks, terminal_id: str, variable: str) -> ast.stmt:
    """``<variable> = live_io.read(<Enum>.<MEMBER>, <variable>)``, the member of
    the control ``terminal_id``."""
    args = [hooks.member(terminal_id), ast.Name(id=variable, ctx=ast.Load())]
    return _live_io_call("read", args, variable)


def reads_in_frame(
    ctx: CodeGenContext, frame: tuple[str, str | int | None]
) -> list[ast.stmt]:
    """The reads that start the diagram of the structure frame ``frame``: each
    control whose terminal sits in that frame inside a loop or Event Structure
    is read again at every pass."""
    hooks, graph = ctx.panel_hooks, ctx.graph
    if hooks is None or graph is None:
        return []
    statements: list[ast.stmt] = []
    for terminal_id in hooks.controls:
        terminal = graph.get_terminal(terminal_id)
        if (
            isinstance(terminal, FPTerminal)
            and graph.front_panel_loop(terminal_id) is not None
            and (terminal.parent, terminal.frame) == frame
        ):
            variable = ctx.resolve(terminal_id)
            if variable is not None:
                statements.append(read_stmt(hooks, terminal_id, variable))
                statements.extend(event_value_stmts(ctx, frame, terminal_id, variable))
    return statements


def event_value_stmts(
    ctx: CodeGenContext,
    frame: tuple[str, str | int | None],
    terminal_id: str,
    variable: str,
) -> list[ast.stmt]:
    """What a Value Change frame sees on the terminal of the control it handles:
    the event's new value, not the control's value as the panel holds it by now
    (``<variable> = event[1]``). A frame that handles other events too only takes
    the new value when the message comes from this control."""
    hooks, graph = ctx.panel_hooks, ctx.graph
    message = ctx.event_messages.get(frame[0])
    node = graph.get_graph_node(frame[0]) if graph is not None else None
    if hooks is None or message is None or not isinstance(node, EventStructureNode):
        return []
    events = node.frames[int(frame[1] or 0)].events
    changes = [
        e
        for e in events
        if e.source_terminal_id == terminal_id and e.type_code == VALUE_CHANGE_CODE
    ]
    if not changes:
        return []
    assign = build_assign(variable, parse_expr(f"{message}[{NEW_VALUE}]"))
    if len(events) == 1:
        return [assign]
    member = ast.unparse(hooks.member(terminal_id))
    test = parse_expr(f"{message}[{NAME}] == {member}")
    return [ast.If(test=test, body=[assign], orelse=[])]


def has_value_change_frame(ctx: CodeGenContext, terminal_id: str) -> bool:
    """Whether an Event Structure of the VI has a frame that handles a Value Change
    of the control with terminal ``terminal_id``."""
    graph = ctx.graph
    if graph is None or not ctx.vi_name:
        return False
    return any(
        e.source_terminal_id == terminal_id and e.type_code == VALUE_CHANGE_CODE
        for node in graph.iter_nodes(ctx.vi_name)
        if isinstance(node, EventStructureNode)
        for frame in node.frames
        for e in frame.events
    )


def entry_events(ctx: CodeGenContext) -> list[ast.stmt]:
    """``events = live_io.events`` at entry of a VI with an Event Structure: the
    one queue its Event Structures and Register For Events nodes share
    (``nodes/event_user.py``); it stays ``None`` until the first that needs it
    makes one, when no panel passed its queue. The variable is ``events``, or a
    numbered form when the VI already has that name (``ctx.events_var``)."""
    hooks = ctx.panel_hooks
    if hooks is None or hooks.event_enum_name is None:
        return []
    ctx.events_var = ctx.claim_name(EVENTS)
    return [parse_stmt(f"{ctx.events_var} = {LIVE_IO}.events")]


def entry_reads(ctx: CodeGenContext, off_pane: list[Terminal]) -> list[ast.stmt]:
    """The reads at entry of the off-pane controls ``off_pane`` that the
    diagram reads (a wire leaves the terminal), after their saved defaults."""
    hooks, graph = ctx.panel_hooks, ctx.graph
    if hooks is None or graph is None:
        return []
    statements: list[ast.stmt] = []
    for control in off_pane:
        if control.id in hooks.names and graph.wire_readers(control.id):
            variable = ctx.resolve(control.id) or hooks.names[control.id]
            statements.append(read_stmt(hooks, control.id, variable))
    return statements
