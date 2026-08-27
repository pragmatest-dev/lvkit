"""Round-trip parser for the lvnet text surface (``render_lvnet``).

See ``docs/_internal/design/netlist-language.md`` §2 (skeleton), §3 (the
terminal-line grammar), §4 (the ``=``/``default`` binding operator), §5 (the
``wiring_rule`` keyword), §7 (nodes), §8 (structures), §9 (net naming) for the
grammar this parses against. The losslessness gate for the whole lvnet
surface is ``parse_lvnet(render_lvnet(module, verbose=True))`` reproducing
``module``'s semantic content -- ``boundary_signature``/``netlist_signature``
are the comparable projections that gate compares.

Increment 1 built the harness on the boundary block only. Increment 2 (this
pass) grows it to the BODY: node declarations, their terminal lines, net
references, and the CLOSED case/for-loop/while-loop/shift-register/tunnel
constructs (per the coordinator's explicit scope). Sequence/disabled/event
structures are NOT yet covered -- encountering one raises ``LvnetParseError``
naming it, rather than guessing a shape for an unimplemented construct.

Parsing is grammar-aware, not regex-guesswork over the whole line: every line
is split on the SAME structural markers ``render_lvnet`` composes it from
(`` : `` opens a type/component clause; `` = `` opens a driver/source clause;
a trailing ``default [<value>]``/`` ; inverted`` clause is peeled off the
right) -- never a single "parse the whole line" regex. Indentation is
tracked exactly (2 spaces per nesting level, per §2) rather than inferred. A
line that doesn't fit this grammar raises ``LvnetParseError`` naming the
exact line, per this project's "never silently skip" rule -- there is no
silent best-effort fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import ScalarValue
from .netlist import (
    _LVNET_INSTANCE_KEYWORDS,
    _LVNET_TUNNEL_MODE_WORD,
    _OPEN_INSTANCE_TRAILING_TODO,
    ConnectorPaneTerminal,
    EtaMerge,
    GammaMerge,
    MuMerge,
    NetlistConstant,
    NetlistFeedback,
    NetlistInstance,
    NetlistInstanceKind,
    NetlistItem,
    NetlistModule,
    NetlistScope,
    _assign_lvnet_handles,
    _is_void_type,
    _lvnet_component,
    _lvnet_default_trailing,
    _lvnet_net_separator,
    _lvnet_requirement_trailing,
    _lvnet_scalar_value_token,
    _lvnet_type_label,
    _LvnetHandles,
    _quoted_frame_label,
    _render_lvnet_source,
)

# lvnet §5's three real dispositions -- ``unknown`` never renders a keyword
# at all (terse and verbose both omit it, see ``_lvnet_requirement_trailing``),
# so it is never a token this parser needs to recognize on a BOUNDARY line
# (node/call-site terminal lines never carry this axis at all this pass).
_REQUIREMENT_WORDS = frozenset({"required", "recommended", "optional"})

_HEADER_RE = re.compile(r"^vi (.+) :$")
# A boundary terminal line: 2-space block indent (§2), then the 3-char
# ``in ``/``out`` keyword, then at least one space, then everything else
# (name/type/requirement/default -- split out below).
_BOUNDARY_LINE_RE = re.compile(r"^  (in|out)\s+(.+)$")
# The SAME ``in ``/``out`` shape, but matched against already indent-stripped
# CONTENT (variable indent -- a node's own terminal block nests at whatever
# depth its declaration sits at), reused for every node's terminal block.
_TERMINAL_CONTENT_RE = re.compile(r"^(in|out)\s+(.+)$")

# The §7 header keywords a node-DECLARATION line can open with (reusing
# ``render_lvnet``'s OWN keyword table directly, rather than a second
# hand-maintained list that could drift from it).
_NODE_KEYWORDS: tuple[str, ...] = tuple(_LVNET_INSTANCE_KEYWORDS.values())

# Structure keywords this increment does NOT yet parse (§8's sequence/
# disabled/event families) -- recognized ONLY so encountering one raises a
# precise, named error instead of falling through to "unrecognized line".
_UNSUPPORTED_STRUCTURE_HEADERS = frozenset(
    {
        "flat-sequence :",
        "stacked-sequence :",
        "diagram-disable :",
        "conditional-disable :",
        "type-specialization :",
        "event-structure :",
    }
)


class LvnetParseError(ValueError):
    """A line did not fit the lvnet grammar this parser knows -- OR a
    recognized-but-not-yet-implemented construct was encountered (e.g. an
    event/sequence/disable structure, §8 -- out of THIS increment's scope,
    per the coordinator's explicit case/loop-only ask). Always names the
    offending line/construct -- this parser never silently skips or
    guesses a shape it hasn't verified against real rendered text.
    """


class LvnetUnsupportedConstructError(NotImplementedError):
    """Raised by the MODULE-side ``netlist_signature`` builder when it meets
    a construct this increment's parser also can't parse (sequence/disabled/
    event scopes) -- kept symmetric with ``LvnetParseError`` so a VI
    exercising one of these fails LOUDLY on EITHER side of the round-trip,
    never silently by producing a signature the other side can't match.
    """


# ============================================================
# Boundary block (increment 1)
# ============================================================


@dataclass(frozen=True)
class ParsedBoundaryTerminal:
    """One parsed ``in ``/``out`` boundary line (lvnet §3's terminal-line
    grammar, applied to the VI's own connector pane): ``(name, type,
    direction, requirement, default)`` -- the same 5 facts §3 composes a
    terminal line from, minus the ``= <driver>`` clause (§2: a boundary line
    never carries one -- it's the pane CONTRACT, not a wire).

    ``requirement`` is the bare §5 keyword text (``"required"`` /
    ``"recommended"`` / ``"optional"``), or ``None`` when the line carries
    none (terse mode, or an ``unknown``/unresolved wiring rule -- both
    render identically, see ``_lvnet_requirement_trailing``).

    ``default`` is the raw VALUE text following the ``default`` keyword
    (e.g. ``'""'``, ``'"1"'``), the literal string ``"default"`` for the BARE
    keyword (§4: a class/refnum type with no literal default), or ``None``
    when the line carries no default clause at all.
    """

    name: str
    type: str
    direction: str  # "in" | "out"
    requirement: str | None
    default: str | None


def _split_type_requirement_default(
    tail: str, line_no: int, line: str
) -> tuple[str, str | None, str | None]:
    """Split a BOUNDARY terminal line's post-``:`` tail into ``(type,
    requirement, default)`` per §3's ``<Type> [<requirement>] [default
    <value>]`` order -- no ``= <driver>`` clause is ever expected here (§2:
    the pane is a contract, not a wire).

    Tokenizes on whitespace RUNS (``str.split()``), which collapses the
    column-alignment padding ``_render_term_group`` inserts back down to
    single spaces -- safe because the actual VALUE text itself is assumed to
    never contain more than one consecutive space (an assumption, not a
    proof -- flagged in the round-trip report). The ``default`` keyword is
    found by its LAST occurrence (the default clause is always the
    RIGHTMOST clause on a terminal line, §3), so a value that happens to
    contain the word "default" earlier does not fool the split.
    """
    words = tail.split()
    if "=" in words:
        raise LvnetParseError(
            f"line {line_no}: boundary terminal line must not carry a "
            f"'= <driver>' clause (§2: the pane is a contract, not a wire): "
            f"{line!r}"
        )
    if not words:
        raise LvnetParseError(f"line {line_no}: missing type after ':': {line!r}")

    default: str | None = None
    if "default" in words:
        idx = len(words) - 1 - words[::-1].index("default")
        value_words = words[idx + 1 :]
        default = " ".join(value_words) if value_words else "default"
        words = words[:idx]

    requirement: str | None = None
    if words and words[-1] in _REQUIREMENT_WORDS:
        requirement = words.pop()

    if not words:
        raise LvnetParseError(
            f"line {line_no}: empty type after stripping requirement/default "
            f"keywords: {line!r}"
        )
    return " ".join(words), requirement, default


def _split_node_terminal_tail(
    tail: str, line_no: int, line: str
) -> tuple[str, str | None, str | None, bool]:
    """Split a NODE terminal line's post-``:`` tail into ``(type, driver,
    default, inverted)`` -- the node-call counterpart of
    ``_split_type_requirement_default``: every INPUT line carries EXACTLY
    one of ``= <driver>`` / ``default <value>`` (never both, never neither
    -- ``_render_lvnet_instance``'s own invariant), optionally suffixed
    `` ; inverted`` (§6); an OUTPUT line carries neither. No requirement
    keyword ever appears here (§11: "the wiring_rule nuance at call sites"
    is a later slice).
    """
    text = tail
    inverted = False
    if text.endswith(" ; inverted"):
        text = text[: -len(" ; inverted")]
        inverted = True

    words = text.split()
    if not words:
        raise LvnetParseError(f"line {line_no}: missing type after ':': {line!r}")

    driver: str | None = None
    default: str | None = None
    if "=" in words:
        idx = len(words) - 1 - words[::-1].index("=")
        value_words = words[idx + 1 :]
        if not value_words:
            raise LvnetParseError(
                f"line {line_no}: '=' with no driver value: {line!r}"
            )
        driver = " ".join(value_words)
        words = words[:idx]
    elif "default" in words:
        idx = len(words) - 1 - words[::-1].index("default")
        value_words = words[idx + 1 :]
        default = " ".join(value_words) if value_words else "default"
        words = words[:idx]

    if not words:
        raise LvnetParseError(
            f"line {line_no}: empty type after stripping '='/'default': {line!r}"
        )
    return " ".join(words), driver, default, inverted


# ============================================================
# Body IR
# ============================================================


@dataclass(frozen=True)
class ParsedTerminalLine:
    """One ``in ``/``out`` terminal line inside a node's own block (§3, the
    node-call form): ``name : type`` plus EXACTLY one of ``driver``
    (`` = <net-or-literal>``) or ``default`` (unwired), or neither for an
    OUTPUT line. ``inverted`` is the §6 `` ; inverted`` annotation.
    """

    name: str
    type: str
    direction: str  # "in" | "out"
    driver: str | None
    default: str | None
    inverted: bool = False


@dataclass(frozen=True)
class ParsedNode:
    """One node declaration (§7): ``<kind> <handle> : <component>`` plus its
    terminal block -- OR, for ``local-variable`` (§7's one still-fully-
    deferred kind), a bare keyword with no handle/component at all.

    ``has_todo`` is whether a trailing ``# TODO(lvnet): ...`` line followed
    the terminal block (expected for ``in-place-element``/``formula-node``/
    ``local-variable``; unexpected for anything else -- ``netlist_signature``
    comparison catches a mismatch here, this dataclass just records the
    plain fact).
    """

    kind: str  # the §7 header keyword, e.g. "subVI", "function", "local-variable"
    handle: str | None
    component: str | None
    terminals: tuple[ParsedTerminalLine, ...] = ()
    has_todo: bool = False


@dataclass(frozen=True)
class ParsedConstant:
    """``constant <handle> : <Type> = <value>`` (§7) -- a single line, no
    terminal block."""

    handle: str
    type: str
    value: str


@dataclass(frozen=True)
class ParsedDrive:
    """A bare ``<net> = <source>`` line -- a case frame's own contribution
    to a case-output tunnel (§8), OR (at the very end of the document,
    outside any scope) a VI boundary output being driven (§2)."""

    net: str
    source: str


@dataclass(frozen=True)
class ParsedShiftRegister:
    """``shift-register <net> :`` + its ``init``/``each`` lines (§8)."""

    net: str
    init: str
    each: str | None


@dataclass(frozen=True)
class ParsedTunnel:
    """``tunnel <net> : <mode> = <source>`` (§8) -- a single line."""

    net: str
    mode: str
    source: str


@dataclass(frozen=True)
class ParsedFeedback:
    """``feedback-node <net> (<attribute>) :`` + its ``init``/``each`` lines
    (§7)."""

    net: str
    attribute: str  # e.g. "1 iteration", "3 iterations", "? iterations"
    init: str
    each: str | None


@dataclass(frozen=True)
class ParsedFrame:
    """One case frame: its (already-quoted, verbatim) display label, its own
    body items, and its own case-output-drive lines (§8: "each frame
    declares what it drives ... inside the frame")."""

    label: str  # kept WITH its surrounding quotes, verbatim as rendered
    body: tuple[ParsedBodyItem, ...] = ()
    drives: tuple[ParsedDrive, ...] = ()


@dataclass(frozen=True)
class ParsedScope:
    """A structure (§8) -- CLOSED kinds only this increment: ``"case"`` /
    ``"for-loop"`` / ``"while-loop"``.

    ``case`` uses ``selector`` + ``frames`` (frame headers + per-frame
    drives); a loop uses ``body`` directly (its single implicit body, §2)
    plus its own ``shift_registers``/``tunnels`` border constructs (§8),
    rendered as siblings of ``body``'s own items. The two shapes don't
    overlap in practice (a loop's ``frames`` is always empty; a case's
    ``body``/``shift_registers``/``tunnels`` are always empty) -- kept as
    one dataclass because both are "a structure with a kind and contents",
    not because the fields are meaningful together.
    """

    kind: str  # "case" | "for-loop" | "while-loop"
    selector: str | None = None  # case only
    frames: tuple[ParsedFrame, ...] = ()  # case only
    body: tuple[ParsedBodyItem, ...] = ()  # loop only
    shift_registers: tuple[ParsedShiftRegister, ...] = ()  # loop only
    tunnels: tuple[ParsedTunnel, ...] = ()  # loop only


ParsedBodyItem = ParsedNode | ParsedScope | ParsedFeedback | ParsedConstant


@dataclass(frozen=True)
class ParsedLvnet:
    """The result of parsing an lvnet text: header, boundary block, body,
    and the final boundary-output-drive block."""

    vi_name: str
    boundary: tuple[ParsedBoundaryTerminal, ...] = field(default_factory=tuple)
    body: tuple[ParsedBodyItem, ...] = field(default_factory=tuple)
    output_drives: tuple[ParsedDrive, ...] = field(default_factory=tuple)


# ============================================================
# Line-oriented cursor + shared helpers
# ============================================================


class _Cursor:
    """A position into the text's ``splitlines()`` list -- shared by every
    parsing function below so nested constructs (a case frame's own body, a
    node's own terminal block) can consume exactly as many lines as they
    need and hand control back to their caller."""

    __slots__ = ("lines", "pos")

    def __init__(self, lines: list[str], pos: int = 0) -> None:
        self.lines = lines
        self.pos = pos

    @property
    def line_no(self) -> int:
        return self.pos + 1

    def peek(self) -> str | None:
        return self.lines[self.pos] if self.pos < len(self.lines) else None

    def take(self) -> str:
        line = self.lines[self.pos]
        self.pos += 1
        return line


def _indent_len(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _expect_kv_line(
    cursor: _Cursor, indent: int, keyword: str, *, required: bool
) -> str | None:
    """Read one ``<keyword> = <value>`` child line (a shift-register's or
    feedback-node's ``init``/``each``) at EXACTLY ``indent`` spaces, or
    return/raise per ``required`` when it's absent."""
    line = cursor.peek()
    prefix = f"{keyword} = "
    if line is None or line.strip() == "" or _indent_len(line) != indent:
        if required:
            raise LvnetParseError(
                f"line {cursor.line_no}: expected a {prefix!r} line, "
                f"found end of block"
            )
        return None
    content = line[indent:]
    if not content.startswith(prefix):
        if required:
            raise LvnetParseError(
                f"line {cursor.line_no}: expected {prefix!r}, got {line!r}"
            )
        return None
    cursor.take()
    return content[len(prefix) :]


def _parse_terminal_block(
    cursor: _Cursor, indent: int
) -> tuple[list[ParsedTerminalLine], bool]:
    """Read a node's own ``in ``/``out`` terminal block at EXACTLY
    ``indent`` spaces, stopping at the first line that ISN'T one (a dedent,
    a blank line, or a ``# TODO(lvnet): ...`` block-end for in-place-
    element/formula-node/local-variable, §7). Any OTHER same-indent line is
    a genuine grammar violation -- raised, never silently skipped."""
    terminals: list[ParsedTerminalLine] = []
    has_todo = False
    while True:
        line = cursor.peek()
        if line is None or line.strip() == "" or _indent_len(line) != indent:
            break
        content = line[indent:]
        if content.startswith("# TODO(lvnet):"):
            cursor.take()
            has_todo = True
            break
        m = _TERMINAL_CONTENT_RE.match(content)
        if m is None:
            raise LvnetParseError(
                f"line {cursor.line_no}: expected an 'in '/'out' terminal "
                f"line or a '# TODO(lvnet): ...' block-end, got {line!r}"
            )
        line_no = cursor.line_no
        cursor.take()
        direction, rest = m.group(1), m.group(2)
        sep_idx = rest.find(" : ")
        if sep_idx == -1:
            raise LvnetParseError(
                f"line {line_no}: missing ' : <Type>' clause (§3): {line!r}"
            )
        name = rest[:sep_idx].strip()
        tail = rest[sep_idx + 3 :]
        if not name:
            raise LvnetParseError(f"line {line_no}: empty terminal name: {line!r}")
        type_str, driver, default, inverted = _split_node_terminal_tail(
            tail, line_no, line
        )
        terminals.append(
            ParsedTerminalLine(
                name=name,
                type=type_str,
                direction=direction,
                driver=driver,
                default=default,
                inverted=inverted,
            )
        )
    return terminals, has_todo


# ============================================================
# Body item parsing
# ============================================================


def _parse_node(
    cursor: _Cursor, indent: int, kw: str, content: str, line_no: int
) -> ParsedNode:
    rest = content[len(kw) + 1 :]
    sep_idx = rest.find(" : ")
    if sep_idx == -1:
        raise LvnetParseError(
            f"line {line_no}: node declaration missing ' : <component>' "
            f"(§7): {content!r}"
        )
    handle = rest[:sep_idx]
    component = rest[sep_idx + 3 :]
    if not handle or " " in handle:
        raise LvnetParseError(
            f"line {line_no}: node handle must be a single space-free "
            f"token (§7/§9): {content!r}"
        )
    terminals, has_todo = _parse_terminal_block(cursor, indent + 2)
    return ParsedNode(
        kind=kw,
        handle=handle,
        component=component,
        terminals=tuple(terminals),
        has_todo=has_todo,
    )


def _finish_local_variable(cursor: _Cursor, indent: int, line_no: int) -> ParsedNode:
    todo_indent = indent + 2
    line = cursor.peek()
    if (
        line is None
        or _indent_len(line) != todo_indent
        or not line[todo_indent:].startswith("# TODO(lvnet):")
    ):
        raise LvnetParseError(
            f"line {line_no}: 'local-variable' must be followed by a "
            f"'# TODO(lvnet): ...' placeholder line (§7)"
        )
    cursor.take()
    return ParsedNode(kind="local-variable", handle=None, component=None, has_todo=True)


def _parse_constant_line(content: str, line_no: int) -> ParsedConstant:
    rest = content[len("constant ") :]
    sep_idx = rest.find(" : ")
    if sep_idx == -1:
        raise LvnetParseError(
            f"line {line_no}: constant line missing ' : <Type>' (§7): {content!r}"
        )
    handle = rest[:sep_idx]
    tail = rest[sep_idx + 3 :]
    eq_idx = tail.find(" = ")
    if eq_idx == -1:
        raise LvnetParseError(
            f"line {line_no}: constant line missing ' = <value>' (§7): {content!r}"
        )
    return ParsedConstant(handle=handle, type=tail[:eq_idx], value=tail[eq_idx + 3 :])


def _parse_feedback(
    cursor: _Cursor, indent: int, content: str, line_no: int
) -> ParsedFeedback:
    rest = content[len("feedback-node ") :]
    if not rest.endswith(" :"):
        raise LvnetParseError(
            f"line {line_no}: feedback-node header must end with ' :' (§7): "
            f"{content!r}"
        )
    rest = rest[: -len(" :")]
    open_paren = rest.find(" (")
    if open_paren == -1 or not rest.endswith(")"):
        raise LvnetParseError(
            f"line {line_no}: feedback-node header missing "
            f"'(<N> iteration[s])' (§7): {content!r}"
        )
    net = rest[:open_paren]
    attribute = rest[open_paren + 2 : -1]
    child_indent = indent + 2
    init = _expect_kv_line(cursor, child_indent, "init", required=True)
    assert init is not None
    each = _expect_kv_line(cursor, child_indent, "each", required=False)
    return ParsedFeedback(net=net, attribute=attribute, init=init, each=each)


def _parse_shift_register(
    cursor: _Cursor, body_indent: int, content: str, line_no: int
) -> ParsedShiftRegister:
    rest = content[len("shift-register ") :]
    if not rest.endswith(" :"):
        raise LvnetParseError(
            f"line {line_no}: shift-register header must end with ' :' "
            f"(§8): {content!r}"
        )
    net = rest[: -len(" :")]
    child_indent = body_indent + 2
    init = _expect_kv_line(cursor, child_indent, "init", required=True)
    assert init is not None
    each = _expect_kv_line(cursor, child_indent, "each", required=False)
    return ParsedShiftRegister(net=net, init=init, each=each)


def _parse_tunnel(content: str, line_no: int) -> ParsedTunnel:
    rest = content[len("tunnel ") :]
    sep_idx = rest.find(" : ")
    if sep_idx == -1:
        raise LvnetParseError(
            f"line {line_no}: tunnel line missing ' : <mode>' (§8): {content!r}"
        )
    net = rest[:sep_idx]
    tail = rest[sep_idx + 3 :]
    eq_idx = tail.find(" = ")
    if eq_idx == -1:
        raise LvnetParseError(
            f"line {line_no}: tunnel line missing ' = <source>' (§8): {content!r}"
        )
    return ParsedTunnel(net=net, mode=tail[:eq_idx], source=tail[eq_idx + 3 :])


def _parse_loop_scope(
    cursor: _Cursor, indent: int, content: str, line_no: int
) -> ParsedScope:
    kind = "while-loop" if content == "while-loop :" else "for-loop"
    body_indent = indent + 2
    body, drives = _parse_items(
        cursor, body_indent, stop_prefixes=("shift-register ", "tunnel ")
    )
    if drives:
        raise LvnetParseError(
            f"line {line_no}: a {kind} body must not contain bare "
            f"'net = source' drive lines (those belong to a case frame, "
            f"§8): {drives!r}"
        )
    shift_registers: list[ParsedShiftRegister] = []
    tunnels: list[ParsedTunnel] = []
    while True:
        line = cursor.peek()
        if line is None or line.strip() == "" or _indent_len(line) != body_indent:
            break
        line_content = line[body_indent:]
        cur_line_no = cursor.line_no
        if line_content.startswith("shift-register "):
            cursor.take()
            shift_registers.append(
                _parse_shift_register(cursor, body_indent, line_content, cur_line_no)
            )
        elif line_content.startswith("tunnel "):
            cursor.take()
            tunnels.append(_parse_tunnel(line_content, cur_line_no))
        else:
            break
    return ParsedScope(
        kind=kind,
        body=tuple(body),
        shift_registers=tuple(shift_registers),
        tunnels=tuple(tunnels),
    )


def _parse_case_scope(
    cursor: _Cursor, indent: int, content: str, line_no: int
) -> ParsedScope:
    selector = content[len("case ") : -len(" :")]
    frame_indent = indent + 2
    body_indent = indent + 4
    frames: list[ParsedFrame] = []
    while True:
        line = cursor.peek()
        if line is None or line.strip() == "" or _indent_len(line) != frame_indent:
            break
        frame_line_no = cursor.line_no
        frame_line = cursor.take()
        frame_content = frame_line[frame_indent:]
        if not (frame_content.startswith('frame "') and frame_content.endswith('" :')):
            raise LvnetParseError(
                f"line {frame_line_no}: expected 'frame \"<label>\" :' "
                f"inside a case scope (§8), got {frame_line!r}"
            )
        label = frame_content[len("frame ") : -len(" :")]  # keeps its quotes
        items, drives = _parse_items(cursor, body_indent)
        frames.append(ParsedFrame(label=label, body=tuple(items), drives=tuple(drives)))
    if not frames:
        raise LvnetParseError(
            f"line {line_no}: case scope has no frames (§8): {content!r}"
        )
    return ParsedScope(kind="case", selector=selector, frames=tuple(frames))


def _parse_one_item_or_drive(
    cursor: _Cursor, indent: int, content: str, line_no: int
) -> ParsedBodyItem | ParsedDrive:
    if content == "local-variable":
        return _finish_local_variable(cursor, indent, line_no)
    if content.startswith("constant "):
        return _parse_constant_line(content, line_no)
    if content.startswith("feedback-node "):
        return _parse_feedback(cursor, indent, content, line_no)
    for kw in _NODE_KEYWORDS:
        if content.startswith(kw + " "):
            return _parse_node(cursor, indent, kw, content, line_no)
    if content in ("for-loop :", "while-loop :"):
        return _parse_loop_scope(cursor, indent, content, line_no)
    if content.startswith("case ") and content.endswith(" :"):
        return _parse_case_scope(cursor, indent, content, line_no)
    if content in _UNSUPPORTED_STRUCTURE_HEADERS:
        raise LvnetParseError(
            f"line {line_no}: structure {content!r} is not yet supported by "
            f"this parser increment (scope: case/for-loop/while-loop only, "
            f"per the round-trip harness build-out -- see the report)"
        )
    if " = " in content:
        net, _, source = content.partition(" = ")
        return ParsedDrive(net=net, source=source)
    raise LvnetParseError(f"line {line_no}: unrecognized body line: {content!r}")


def _should_stop(cursor: _Cursor, indent: int, stop_prefixes: tuple[str, ...]) -> bool:
    line = cursor.peek()
    if line is None or line.strip() == "":
        return True
    line_indent = _indent_len(line)
    if line_indent < indent:
        return True
    if line_indent > indent:
        raise LvnetParseError(
            f"line {cursor.line_no}: unexpected indentation (expected "
            f"{indent} spaces): {line!r}"
        )
    content = line[indent:]
    return any(content.startswith(p) for p in stop_prefixes)


def _parse_items(
    cursor: _Cursor, indent: int, stop_prefixes: tuple[str, ...] = ()
) -> tuple[list[ParsedBodyItem], list[ParsedDrive]]:
    """Parse a run of sibling body items (+ any bare drive lines interleaved
    among them, §8's per-frame case-output drives) at EXACTLY ``indent``
    spaces, stopping at a dedent, a blank line, or a line starting with one
    of ``stop_prefixes`` (used by a loop scope to hand control back once its
    body ends and its own ``shift-register``/``tunnel`` siblings begin)."""
    items: list[ParsedBodyItem] = []
    drives: list[ParsedDrive] = []
    while not _should_stop(cursor, indent, stop_prefixes):
        line_no = cursor.line_no
        line = cursor.take()
        content = line[indent:]
        result = _parse_one_item_or_drive(cursor, indent, content, line_no)
        if isinstance(result, ParsedDrive):
            drives.append(result)
        else:
            items.append(result)
    return items, drives


# ============================================================
# Top-level parse
# ============================================================


def _parse_boundary_block(cursor: _Cursor) -> list[ParsedBoundaryTerminal]:
    boundary: list[ParsedBoundaryTerminal] = []
    while True:
        line = cursor.peek()
        if line is None:
            break
        if line.strip() == "":
            cursor.take()  # the blank line ending the boundary block (§2)
            break
        line_no = cursor.line_no
        cursor.take()
        m = _BOUNDARY_LINE_RE.match(line)
        if m is None:
            raise LvnetParseError(
                f"line {line_no}: expected an 'in '/'out' boundary terminal "
                f"line or the blank line ending the boundary block, got "
                f"{line!r}"
            )
        direction, rest = m.group(1), m.group(2)
        # Split on the STRUCTURAL ``" : "`` (space-colon-space) token, never
        # a bare ``":"``: a terminal's own authored name can itself contain
        # a literal colon with no preceding space (a real corpus control
        # named ``"txtRuns:"``), which a first-bare-":" split mis-splits.
        sep_idx = rest.find(" : ")
        if sep_idx == -1:
            raise LvnetParseError(
                f"line {line_no}: missing ' : <Type>' clause (§3): {line!r}"
            )
        name = rest[:sep_idx].strip()
        tail = rest[sep_idx + 3 :]
        if not name:
            raise LvnetParseError(f"line {line_no}: empty terminal name: {line!r}")
        type_str, requirement, default = _split_type_requirement_default(
            tail, line_no, line
        )
        boundary.append(
            ParsedBoundaryTerminal(
                name=name,
                type=type_str,
                direction=direction,
                requirement=requirement,
                default=default,
            )
        )
    return boundary


def _parse_output_drives(cursor: _Cursor) -> list[ParsedDrive]:
    drives: list[ParsedDrive] = []
    while True:
        line = cursor.peek()
        if line is None:
            break
        if line.strip() == "":
            cursor.take()
            continue
        line_no = cursor.line_no
        cursor.take()
        if _indent_len(line) != 2:
            raise LvnetParseError(
                f"line {line_no}: output-drive line must be at 2-space "
                f"indent (§2): {line!r}"
            )
        content = line[2:]
        eq_idx = content.find("=")
        if eq_idx == -1:
            raise LvnetParseError(
                f"line {line_no}: expected '<name> = <source>' output-drive "
                f"line, got {line!r}"
            )
        name = content[:eq_idx].rstrip()
        source = content[eq_idx + 1 :].lstrip()
        if not name:
            raise LvnetParseError(
                f"line {line_no}: empty output name in drive line: {line!r}"
            )
        drives.append(ParsedDrive(net=name, source=source))
    return drives


def parse_lvnet(text: str) -> ParsedLvnet:
    """Parse an lvnet text's ``vi <name> :`` header, boundary block, BODY,
    and final boundary-output-drive block. Raises ``LvnetParseError`` naming
    the exact line on anything that doesn't fit the grammar this increment
    knows -- never silently skips.
    """
    lines = text.splitlines()
    if not lines:
        raise LvnetParseError("empty lvnet text: no 'vi <name> :' header line")

    header_match = _HEADER_RE.match(lines[0])
    if header_match is None:
        raise LvnetParseError(
            f"line 1: expected a 'vi <name> :' header, got {lines[0]!r}"
        )
    vi_name = header_match.group(1)

    cursor = _Cursor(lines, pos=1)
    boundary = _parse_boundary_block(cursor)

    body_items, stray_drives = _parse_items(cursor, indent=2)
    if stray_drives:
        raise LvnetParseError(
            f"the top-level VI body must not contain bare 'net = source' "
            f"drive lines outside a case frame (§8): {stray_drives!r}"
        )

    blank = cursor.peek()
    if blank is not None and blank.strip() == "":
        cursor.take()  # the blank line separating body from output-drives (§2)
    output_drives = _parse_output_drives(cursor)

    return ParsedLvnet(
        vi_name=vi_name,
        boundary=tuple(boundary),
        body=tuple(body_items),
        output_drives=tuple(output_drives),
    )


# ============================================================
# Signatures -- the canonical, comparable projections
# ============================================================


def _module_default_token(default: ScalarValue) -> str | None:
    """The comparable ``default`` token for a MODULE-side BOUNDARY terminal
    (``ConnectorPaneTerminal.default``, a raw ``ScalarValue``) -- ``None``
    when the pane genuinely has no default recorded, else the exact literal
    text ``render_lvnet`` now emits for it (Gap #1, closed: see
    ``_lvnet_scalar_value_token`` in ``netlist.py``)."""
    if default is None:
        return None
    return _lvnet_scalar_value_token(default)


def boundary_signature(
    module_or_parsed: NetlistModule | ParsedLvnet,
) -> tuple[tuple[str, str, str, str | None, str | None], ...]:
    """The canonical, comparable boundary projection: an ordered tuple of
    ``(name, type_string, direction, wiring_requirement_or_None,
    default_token_or_None)`` per terminal (inputs then outputs) -- computed
    IDENTICALLY (same helper calls, same field sources) from a real
    ``NetlistModule`` and from a ``ParsedLvnet``.
    """
    if isinstance(module_or_parsed, ParsedLvnet):
        return tuple(
            (t.name, t.type, t.direction, t.requirement, t.default)
            for t in module_or_parsed.boundary
        )

    module = module_or_parsed
    pane_terminals = module.connector_pane.terminals
    input_panes: list[ConnectorPaneTerminal] = pane_terminals[: len(module.inputs)]
    output_panes: list[ConnectorPaneTerminal] = pane_terminals[
        len(module.inputs) : len(module.inputs) + len(module.outputs)
    ]

    entries: list[tuple[str, str, str, str | None, str | None]] = [
        (
            inp.name,
            _lvnet_type_label(inp.type_descriptor, inp.lv_type),
            "in",
            _lvnet_requirement_trailing(pane),
            _module_default_token(pane.default),
        )
        for inp, pane in zip(module.inputs, input_panes, strict=True)
    ] + [
        (
            o.name,
            _lvnet_type_label(o.type_descriptor, o.lv_type),
            "out",
            _lvnet_requirement_trailing(pane),
            _module_default_token(pane.default),
        )
        for o, pane in zip(module.outputs, output_panes, strict=True)
    ]
    return tuple(entries)


def _strip_default_prefix(rendered: str) -> str:
    """``_lvnet_default_trailing``'s rendered text (``"default"`` or
    ``"default <literal>"``) -> just the value-side token this parser's
    ``ParsedTerminalLine.default`` carries (``"default"`` or ``"<literal>"``
    -- the word itself is not repeated, matching how the boundary side
    already represents this)."""
    if rendered == "default":
        return rendered
    return rendered[len("default ") :]


def _module_terminal_tuple(
    instance: NetlistInstance, handles: _LvnetHandles
) -> tuple[tuple[str, str, str, str | None, str | None, bool], ...]:
    entries: list[tuple[str, str, str, str | None, str | None, bool]] = []
    for b in sorted(instance.inputs, key=lambda b: b.pane_rank):
        if _is_void_type(b.type):
            continue
        type_label = _lvnet_type_label(b.type, b.lv_type)
        if b.net is not None:
            driver = _render_lvnet_source(b.net, handles)
            default = None
        else:
            assert b.default is not None
            driver = None
            default = _strip_default_prefix(_lvnet_default_trailing(b.default))
        entries.append(("in", b.port, type_label, driver, default, b.inverted))
    for o in sorted(instance.outputs, key=lambda o: o.pane_rank):
        if _is_void_type(o.type):
            continue
        type_label = _lvnet_type_label(o.type, o.lv_type)
        entries.append(("out", o.net.port, type_label, None, None, False))
    return tuple(entries)


def _module_instance_signature(
    instance: NetlistInstance, handles: _LvnetHandles
) -> tuple:
    if instance.kind == NetlistInstanceKind.LOCAL_VARIABLE:
        return ("node", "local-variable", None, None, (), True)
    header_kw = _LVNET_INSTANCE_KEYWORDS[instance.kind]
    handle = handles.by_uid[instance.uid]
    component = _lvnet_component(instance)
    terminals = _module_terminal_tuple(instance, handles)
    has_todo = instance.kind in _OPEN_INSTANCE_TRAILING_TODO
    return ("node", header_kw, handle, component, terminals, has_todo)


def _module_constant_signature(const: NetlistConstant, handles: _LvnetHandles) -> tuple:
    handle = handles.by_uid[const.uid]
    return ("constant", handle, const.type, const.value)


def _module_feedback_signature(fb: NetlistFeedback, handles: _LvnetHandles) -> tuple:
    if fb.delay is None:
        attr = "? iterations"
    else:
        attr = f"{fb.delay} iteration" + ("" if fb.delay == 1 else "s")
    init_str = _render_lvnet_source(fb.init, handles)
    each_str = _render_lvnet_source(fb.recur, handles) if fb.recur is not None else None
    return ("feedback", fb.net, attr, init_str, each_str)


def _module_case_scope_signature(scope: NetlistScope, handles: _LvnetHandles) -> tuple:
    sel = (
        _render_lvnet_source(scope.selector, handles)
        if scope.selector is not None
        else "?"
    )
    gammas = [m for m in scope.outputs if isinstance(m, GammaMerge)]
    frames: list[tuple] = []
    for frame in scope.frames:
        label = _quoted_frame_label(frame.label)
        body_sig = _module_body_signature(frame.body, handles)
        frame_key = "default" if frame.is_default else frame.label
        drive_entries: list[tuple[str, str]] = []
        for gamma in gammas:
            case_entry = next(
                (c for c in gamma.cases if c.frame_key == frame_key), None
            )
            if case_entry is None:
                continue
            source_str = _render_lvnet_source(case_entry.source, handles)
            net = _lvnet_net_separator(gamma.net)
            drive_entries.append((net, source_str))
        frames.append((label, body_sig, tuple(drive_entries)))
    return ("scope", "case", sel, tuple(frames))


def _module_loop_scope_signature(scope: NetlistScope, handles: _LvnetHandles) -> tuple:
    kind_word = "while-loop" if scope.kind == "while" else "for-loop"
    body_sig = _module_body_signature(scope.frames[0].body, handles)
    shift_regs: list[tuple[str, str, str | None]] = []
    tunnels: list[tuple[str, str, str]] = []
    for merge in scope.outputs:
        if isinstance(merge, MuMerge):
            net = _lvnet_net_separator(merge.net)
            init_str = _render_lvnet_source(merge.init, handles)
            each_str = (
                _render_lvnet_source(merge.recur, handles)
                if merge.recur is not None
                else None
            )
            shift_regs.append((net, init_str, each_str))
        elif isinstance(merge, EtaMerge):
            mode_word = _LVNET_TUNNEL_MODE_WORD.get(merge.index_mode, merge.index_mode)
            if merge.conditional:
                mode_word += "+conditional"
            value_str = _render_lvnet_source(merge.value, handles)
            net = _lvnet_net_separator(merge.net)
            tunnels.append((net, mode_word, value_str))
    return ("scope", kind_word, None, body_sig, tuple(shift_regs), tuple(tunnels))


def _module_body_signature(items: list[NetlistItem], handles: _LvnetHandles) -> tuple:
    out: list[tuple] = []
    for item in items:
        if isinstance(item, NetlistInstance):
            out.append(_module_instance_signature(item, handles))
        elif isinstance(item, NetlistScope):
            if item.kind == "case":
                out.append(_module_case_scope_signature(item, handles))
            elif item.kind in ("for", "while"):
                out.append(_module_loop_scope_signature(item, handles))
            else:
                raise LvnetUnsupportedConstructError(
                    f"netlist_signature does not yet cover scope kind "
                    f"{item.kind!r} (sequence/disabled/event -- out of this "
                    f"increment's scope, matching parse_lvnet's own limit)"
                )
        elif isinstance(item, NetlistFeedback):
            out.append(_module_feedback_signature(item, handles))
        elif isinstance(item, NetlistConstant):
            out.append(_module_constant_signature(item, handles))
    return tuple(out)


def _parsed_item_signature(item: ParsedBodyItem) -> tuple:
    if isinstance(item, ParsedConstant):
        return ("constant", item.handle, item.type, item.value)
    if isinstance(item, ParsedFeedback):
        return ("feedback", item.net, item.attribute, item.init, item.each)
    if isinstance(item, ParsedScope):
        if item.kind == "case":
            frames = tuple(
                (
                    f.label,
                    tuple(_parsed_item_signature(i) for i in f.body),
                    tuple((d.net, d.source) for d in f.drives),
                )
                for f in item.frames
            )
            return ("scope", "case", item.selector, frames)
        body_sig = tuple(_parsed_item_signature(i) for i in item.body)
        shift_regs = tuple((sr.net, sr.init, sr.each) for sr in item.shift_registers)
        tunnels = tuple((t.net, t.mode, t.source) for t in item.tunnels)
        return ("scope", item.kind, None, body_sig, shift_regs, tunnels)
    if isinstance(item, ParsedNode):
        terminals = tuple(
            (t.direction, t.name, t.type, t.driver, t.default, t.inverted)
            for t in item.terminals
        )
        return (
            "node",
            item.kind,
            item.handle,
            item.component,
            terminals,
            item.has_todo,
        )
    raise TypeError(f"unknown parsed body item: {item!r}")  # pragma: no cover


def netlist_signature(module_or_parsed: NetlistModule | ParsedLvnet) -> tuple:
    """The full, comparable projection of an lvnet document: boundary +
    body + the final boundary-output-drive block -- computed IDENTICALLY
    from a real ``NetlistModule`` (reusing ``render_lvnet``'s own private
    helpers directly, e.g. ``_lvnet_component``/``_render_lvnet_source``/
    ``_lvnet_type_label``, so the module side is provably what the text
    would say) and from a ``ParsedLvnet``. Excludes uid and any derived/
    non-textual field, same principle as ``boundary_signature``.

    Raises ``LvnetUnsupportedConstructError`` (module side) or
    ``LvnetParseError`` (parsed side) on a sequence/disabled/event scope --
    this increment's parser doesn't cover those, so a VI exercising one
    fails loudly on whichever side reaches it first, rather than silently
    producing an incomparable signature.
    """
    if isinstance(module_or_parsed, ParsedLvnet):
        parsed = module_or_parsed
        boundary = boundary_signature(parsed)
        body = tuple(_parsed_item_signature(item) for item in parsed.body)
        drives = tuple((d.net, d.source) for d in parsed.output_drives)
        return (boundary, body, drives)

    module = module_or_parsed
    handles = _assign_lvnet_handles(module)
    boundary = boundary_signature(module)
    body = _module_body_signature(module.body, handles)
    drives = tuple(
        (
            o.name,
            _render_lvnet_source(o.source, handles) if o.source is not None else "?",
        )
        for o in module.outputs
    )
    return (boundary, body, drives)
