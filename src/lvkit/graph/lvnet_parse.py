"""Round-trip parser for the lvnet text surface (``render_lvnet``).

See ``docs/_internal/design/netlist-language.md`` §2 (skeleton), §3 (the
terminal-line grammar), §4 (the ``=``/``default`` binding operator), §5 (the
``wiring_rule`` keyword), §7 (nodes), §8 (structures), §9 (net naming) for the
grammar this parses against. The losslessness gate for the whole lvnet
surface is ``parse_lvnet(render_lvnet(module, verbose=True))`` reproducing
``module``'s semantic content -- ``boundary_signature``/``netlist_signature``
are the comparable projections that gate compares.

Increment 1 built the harness on the boundary block only. Increment 2 grew it
to the BODY: node declarations, their terminal lines, net references, and the
CLOSED case/for-loop/while-loop/shift-register/tunnel constructs. Increment 3
(this pass) adds the three families the Phase-1 model had flattened to a
generic scope: ``flat-sequence``/``stacked-sequence``, ``diagram-disable``/
``conditional-disable``/``type-specialization``, and ``event-structure`` --
see ``_parse_labeled_frames``, shared by all three (none of them carry an
output merge to drive, unlike a case frame). A construct genuinely outside
lvnet's §8 vocabulary still raises ``LvnetParseError`` naming it, rather than
guessing a shape.

Phase 4 (graph-identity round-trip) recovers ``ParsedScope.uid`` from a
header's own OPTIONAL trailing `` (id <uid>)`` annotation (``lvnet_
reconstruct.py``'s stronger gate: not just a matching TEXT projection, but
the SAME structure/node identity the original graph carried) -- see
``_split_scope_header_id``.

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
from typing import Any

from ..models import DisableStructureKind, ScalarValue
from .lvnet_grammar import (
    _BLOCK_DIAGRAM_HEADER_LINE,
    _FRONT_PANEL_HEADER_LINE,
    _LVNET_ANNOTATION_SEP,
    _LVNET_BLOCK_OPEN,
    _LVNET_CLUSTER_OPEN,
    _LVNET_DEFAULT_KEYWORD,
    _LVNET_DEP_PATH_SEP,
    _LVNET_DISABLE_KEYWORD,
    _LVNET_DRIVER_OP,
    _LVNET_ENUM_OPEN,
    _LVNET_INDENT_WIDTH,
    _LVNET_INSTANCE_KEYWORDS,
    _LVNET_PANE_INDEX_PREFIX,
    _LVNET_PATTERN_KEYWORD,
    _LVNET_RING_OPEN,
    _LVNET_SCOPE_ID_PREFIX,
    _LVNET_TUNNEL_MODE_WORD,
    _LVNET_TYPE_SEP,
    _LVNET_TYPEDEF_NAV_PREFIX,
    _OPEN_INSTANCE_TRAILING_TODO,
    _TYPES_HEADER_LINE,
    _USES_HEADER_LINE,
)
from .netlist import (
    ConnectorPaneTerminal,
    DependencyKind,
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
)
from .render_lvnet import (
    _assign_lvnet_handles,
    _is_void_type,
    _lv_type_comparison_shape,
    _lvnet_ambiguous_named_types,
    _lvnet_component,
    _lvnet_default_token,
    _lvnet_default_trailing,
    _lvnet_literal_token,
    _lvnet_net_separator,
    _lvnet_requirement_trailing,
    _LvnetHandles,
    _quoted_frame_label,
    _render_lvnet_source,
)

# lvnet §5's three real dispositions -- ``unknown`` never renders a keyword
# at all (terse and verbose both omit it, see ``_lvnet_requirement_trailing``),
# so it is never a token this parser needs to recognize on a BOUNDARY line
# (node/call-site terminal lines never carry this axis at all this pass).
_REQUIREMENT_WORDS = frozenset({"required", "recommended", "optional"})

_HEADER_RE = re.compile(rf"^vi (.+){_LVNET_BLOCK_OPEN}$")
# The OPTIONAL ``uses :`` dependency-manifest header (new §2/§7 note) --
# immediately after the ``vi <name> :`` header, before the boundary block
# (see ``_parse_uses_block``). Rendered by ``_render_lvnet_uses`` -- reuses
# ``netlist._USES_HEADER_LINE`` directly, never a second hand-spelled copy.
# One dependency entry: 4-space indent, then the kind keyword, then at least
# one space, then the qualified identity (+ optional ``; ./path`` nav).
_USES_ENTRY_RE = re.compile(r"^    (\S+)\s+(.+)$")
# Reuses ``render_lvnet``'s OWN kind enum directly, rather than a second
# hand-maintained word list that could drift from it.
_USES_KIND_WORDS = frozenset(k.value for k in DependencyKind)
# The ``in ``/``out`` terminal-line shape, matched against already
# indent-stripped CONTENT -- reused for every node's own terminal block AND
# (Phase 2) the ``front-panel :`` section's boundary block, both of which
# nest at whatever depth their own header sits at (4 spaces for the
# boundary block, one level deeper than a node's own declaration for a
# node's terminals).
_TERMINAL_CONTENT_RE = re.compile(r"^(in|out)\s+(.+)$")
# Phase 2's unconditional ``@<index>`` trailing column on a boundary
# terminal row (``render_lvnet._lvnet_pane_index_suffix``) -- an ON-PANE
# terminal's connector-pane slot index, always the RIGHTMOST token on the
# line when present (peeled off before requirement/default extraction, see
# ``_split_type_requirement_default``).
_PANE_INDEX_RE = re.compile(rf"^{re.escape(_LVNET_PANE_INDEX_PREFIX)}(\d+)$")

# The §7 header keywords a node-DECLARATION line can open with (reusing
# ``render_lvnet``'s OWN keyword table directly, rather than a second
# hand-maintained list that could drift from it).
_NODE_KEYWORDS: tuple[str, ...] = tuple(_LVNET_INSTANCE_KEYWORDS.values())

# §8's sequence/disabled/event-structure family headers, as rendered
# verbatim by ``_render_lvnet_sequence_scope``/``_render_lvnet_disabled_scope``/
# ``_render_lvnet_event_scope`` -- recognized so ``_parse_one_item_or_drive``
# can dispatch to the matching parser (see ``_parse_sequence_scope``/
# ``_parse_disabled_scope``/``_parse_event_scope`` below).
_SEQUENCE_SCOPE_HEADERS = frozenset(
    {f"flat-sequence{_LVNET_BLOCK_OPEN}", f"stacked-sequence{_LVNET_BLOCK_OPEN}"}
)
_DISABLED_SCOPE_HEADERS = frozenset(
    {
        f"diagram-disable{_LVNET_BLOCK_OPEN}",
        f"conditional-disable{_LVNET_BLOCK_OPEN}",
        f"type-specialization{_LVNET_BLOCK_OPEN}",
    }
)
_EVENT_SCOPE_HEADER = f"event-structure{_LVNET_BLOCK_OPEN}"

# The OPTIONAL bottom-appendix ``types :`` footnote section header (§10,
# verbose-only) -- immediately after the final boundary-output-drive block,
# at the very end of the document (see ``_parse_types_block``). Rendered by
# ``netlist._render_lvnet_types`` -- reuses ``netlist._TYPES_HEADER_LINE``
# directly, never a second hand-spelled copy.


class LvnetParseError(ValueError):
    """A line did not fit the lvnet grammar this parser knows -- every §8
    structure kind is now covered (case/for-loop/while-loop/flat-sequence/
    stacked-sequence/diagram-disable/conditional-disable/type-specialization/
    event-structure), so this now fires only on a genuine grammar violation
    (a malformed line, a missing frame, a stray drive line where this scope
    family has no output to drive). Always names the offending line/
    construct -- this parser never silently skips or guesses a shape it
    hasn't verified against real rendered text.
    """


class LvnetUnsupportedConstructError(NotImplementedError):
    """Raised by the MODULE-side ``netlist_signature`` builder if it ever
    meets a ``NetlistScope.kind`` outside the closed set §8 defines (case/
    for/while/sequence/disabled/event) -- kept symmetric with
    ``LvnetParseError`` so a VI exercising a genuinely new construct fails
    LOUDLY on whichever side reaches it first, never silently by producing a
    signature the other side can't match. Unreachable for any construct this
    module currently builds a ``NetlistScope`` for.
    """


# ============================================================
# String-literal escaping (closes the control-char round-trip gap --
# ``tests/test_lvnet_roundtrip.py``'s former ``Graphical Test Runner``
# xfail)
# ============================================================

# The exact reverse of ``netlist._LVNET_STRING_ESCAPES`` (md §4/§10): every
# two-char escape lvnet's string-literal renderer can emit, mapped back to
# its real character. ``\xHH`` (any OTHER C0 control char) is handled
# separately in ``_unescape_lvnet_string`` since it's three chars wide, not
# two.
_LVNET_STRING_UNESCAPES: dict[str, str] = {
    "\\": "\\",
    '"': '"',
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


def _scan_quoted_literal(text: str, start: int) -> int:
    """Scan an lvnet double-quoted string literal beginning at
    ``text[start] == '"'`` (render_lvnet's own §4/§10 escaping --
    ``netlist._lvnet_literal_token``), honoring backslash escapes, and
    return the index ONE PAST its closing (real, unescaped) quote.

    An escaped ``\\"`` is part of the literal's own text, never mistaken for
    the real close -- so a value containing a literal ``=``/``:``/
    ``default`` substring inside its quotes (a status string like
    ``"5 = 5 is true"``) can never fool a caller's word-based clause
    splitter, and this scan won't stop early on an escaped quote either.
    Raises ``LvnetParseError`` if the quote is never closed on this line --
    a genuine grammar violation, since ``_lvnet_literal_token`` escapes
    every control char to a same-line backslash sequence (lvnet never emits
    a literal spanning physical lines).
    """
    i = start + 1
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            if i + 1 >= n:
                raise LvnetParseError(
                    f"unterminated escape at end of quoted literal: "
                    f"{text[start:]!r}"
                )
            i += 2
            continue
        if ch == '"':
            return i + 1
        i += 1
    raise LvnetParseError(f"unterminated quoted literal: {text[start:]!r}")


def _unescape_lvnet_string(token: str) -> str:
    """Reverse ``netlist._lvnet_literal_token``'s string escaping: a
    double-quoted token (``'"foo\\\\nbar"'``) -> its real value (a genuine
    embedded newline). ``token`` must be exactly the ``"..."`` substring,
    quotes included -- callers isolate it first via ``_scan_quoted_literal``
    (a bare closing-quote match), so this never needs its own boundary
    search. Raises ``LvnetParseError`` on a malformed/unrecognized escape
    (never silently drops or guesses at one)."""
    if len(token) < 2 or not (token.startswith('"') and token.endswith('"')):
        raise LvnetParseError(f"not a quoted lvnet string literal: {token!r}")
    body = token[1:-1]
    out: list[str] = []
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= n:
            raise LvnetParseError(
                f"trailing backslash in lvnet string literal: {token!r}"
            )
        esc = body[i + 1]
        if esc == "x":
            hex_digits = body[i + 2 : i + 4]
            if len(hex_digits) != 2:
                raise LvnetParseError(
                    f"malformed \\x escape in lvnet string literal: {token!r}"
                )
            try:
                out.append(chr(int(hex_digits, 16)))
            except ValueError:
                raise LvnetParseError(
                    f"malformed \\x escape in lvnet string literal: {token!r}"
                ) from None
            i += 4
            continue
        real = _LVNET_STRING_UNESCAPES.get(esc)
        if real is None:
            raise LvnetParseError(
                f"unrecognized escape '\\{esc}' in lvnet string literal: "
                f"{token!r}"
            )
        out.append(real)
        i += 2
    return "".join(out)


def _validate_if_quoted(value: str, line_no: int) -> None:
    """A driver/default/constant VALUE token that (now, md §4/§10) looks
    like a quoted lvnet string literal must actually BE one, end to end --
    catches a truncated or malformed escape at parse time (loudly) instead
    of it silently surviving as opaque text into ``netlist_signature``.
    Every OTHER token shape (a net identifier, ``True``/``False``, a bare
    number) is untouched -- this only fires when the value's own first
    character is a literal-string open quote.
    """
    if not value.startswith('"'):
        return
    end = _scan_quoted_literal(value, 0)
    if end != len(value):
        raise LvnetParseError(
            f"line {line_no}: trailing text after closing quote in value "
            f"token: {value!r}"
        )
    _unescape_lvnet_string(value)  # raises LvnetParseError if malformed


# ============================================================
# Boundary block (increment 1)
# ============================================================


@dataclass(frozen=True)
class ParsedBoundaryTerminal:
    """One parsed ``in ``/``out`` boundary line (lvnet §3's terminal-line
    grammar, applied to the VI's own connector pane): ``(name, type,
    direction, requirement, default, index)`` -- the same facts §3 composes a
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

    ``index`` is the Phase 2 ``@<index>`` pane-slot column
    (``ConnectorPaneTerminal.index``), or ``None`` when the row carried none
    -- an OFF-PANE front-panel control/indicator (a control/indicator that
    exists on the front panel but isn't wired onto the connector pane, see
    ``render_lvnet._lvnet_pane_index_suffix``/``netlist_build.
    _off_pane_terminals``). ``_parse_front_panel_block`` doesn't otherwise
    distinguish an on-pane row from an off-pane one -- both are just
    ``in``/``out`` lines at the same indent -- so this ``index`` field IS
    the on-pane/off-pane discriminator downstream (``boundary_signature``/
    ``reconstruct_module``).
    """

    name: str
    type: str
    direction: str  # "in" | "out"
    requirement: str | None
    default: str | None
    index: int | None = None


def _split_type_requirement_default(
    tail: str, line_no: int, line: str
) -> tuple[str, str | None, str | None, int | None]:
    """Split a BOUNDARY terminal line's post-``:`` tail into ``(type,
    requirement, default, index)`` per §3's ``<Type> [<requirement>]
    [default <value>] [@<index>]`` order -- no ``= <driver>`` clause is ever
    expected here (§2: the pane is a contract, not a wire).

    Tokenizes on whitespace RUNS (``str.split()``), which collapses the
    column-alignment padding ``_render_term_group`` inserts back down to
    single spaces -- safe because the actual VALUE text itself is assumed to
    never contain more than one consecutive space (an assumption, not a
    proof -- flagged in the round-trip report). The Phase 2 ``@<index>``
    column is peeled off FIRST, since it's the OUTERMOST/rightmost token
    when present (``render_lvnet``'s own append order) -- a quoted STRING
    value's own last token always retains its closing quote character, so it
    can never be mistaken for a bare ``@<digits>`` token. The ``default``
    keyword is found by its FIRST occurrence (after that): a ``<Type>``
    clause never legitimately contains the bare word "default" (it's a
    faithful LVType descriptor, never LabVIEW vocabulary), but a STRING
    value legitimately CAN (e.g. a real status literal reading "Restore to
    default settings", md §4/§10 -- now that a string literal's own text
    survives escaped-but-intact) -- so the FIRST occurrence is always the
    real keyword, and everything from there to end of line is the value,
    however many more times the word recurs inside it.
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

    index: int | None = None
    if words:
        m = _PANE_INDEX_RE.match(words[-1])
        if m is not None:
            index = int(m.group(1))
            words = words[:-1]

    if not words:
        raise LvnetParseError(
            f"line {line_no}: empty type after stripping '@index': {line!r}"
        )

    default: str | None = None
    if _LVNET_DEFAULT_KEYWORD in words:
        idx = words.index(_LVNET_DEFAULT_KEYWORD)
        value_words = words[idx + 1 :]
        default = " ".join(value_words) if value_words else _LVNET_DEFAULT_KEYWORD
        words = words[:idx]
        _validate_if_quoted(default, line_no)

    requirement: str | None = None
    if words and words[-1] in _REQUIREMENT_WORDS:
        requirement = words.pop()

    if not words:
        raise LvnetParseError(
            f"line {line_no}: empty type after stripping requirement/default "
            f"keywords: {line!r}"
        )
    return " ".join(words), requirement, default, index


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

    ``=``/``default`` are found by their FIRST occurrence, same reasoning as
    ``_split_type_requirement_default``: a ``<Type>`` clause never contains
    either as a bare word, but a wired STRING literal's own text legitimately
    can (e.g. a driver value reading ``"5 = 5 is true"``, md §4/§10) -- so
    the first occurrence is always the real operator, everything after it
    to end of line is the value however many more times the word/symbol
    recurs inside it.
    """
    text = tail
    inverted = False
    inverted_suffix = f"{_LVNET_ANNOTATION_SEP}inverted"
    if text.endswith(inverted_suffix):
        text = text[: -len(inverted_suffix)]
        inverted = True

    words = text.split()
    if not words:
        raise LvnetParseError(f"line {line_no}: missing type after ':': {line!r}")

    driver: str | None = None
    default: str | None = None
    if "=" in words:
        idx = words.index("=")
        value_words = words[idx + 1 :]
        if not value_words:
            raise LvnetParseError(
                f"line {line_no}: '=' with no driver value: {line!r}"
            )
        driver = " ".join(value_words)
        words = words[:idx]
        _validate_if_quoted(driver, line_no)
    elif _LVNET_DEFAULT_KEYWORD in words:
        idx = words.index(_LVNET_DEFAULT_KEYWORD)
        value_words = words[idx + 1 :]
        default = " ".join(value_words) if value_words else _LVNET_DEFAULT_KEYWORD
        words = words[:idx]
        _validate_if_quoted(default, line_no)

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
    terminal block. ``local-variable`` is NOT one of these -- its ``read``/
    ``write`` shape carries no component and no terminal block, so it parses
    to its own ``ParsedLocalVariable`` instead (see there).

    ``has_todo`` is whether a trailing ``# TODO(lvnet): ...`` line followed
    the terminal block (expected for ``in-place-element``/``formula-node``;
    unexpected for anything else -- ``netlist_signature`` comparison catches
    a mismatch here, this dataclass just records the plain fact).
    """

    kind: str  # the §7 header keyword, e.g. "subVI", "function"
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
class ParsedLocalVariable:
    """``local-variable <handle> : read`` (a SOURCE) or ``local-variable
    <handle> : write = <source>`` (a SINK) -- §7, now designed. A single
    line, no component and no terminal block (the tapped control's own type
    is already spelled at its ``front-panel :`` row, resolved there by
    name -- repeating it here would be redundant)."""

    handle: str
    is_write: bool
    source: str | None = None  # write only; None for a read


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
    # Case-scope ONLY (§8's ``"Error", default`` convention -- always
    # ``False`` for the frame-only families, which don't encode this in
    # their header): recovered by ``_parse_case_frame_header`` from the
    # header's trailing/sole ``default`` list entry, matching
    # ``NetlistFrame.is_default`` on the build side so
    # ``lvnet_reconstruct``'s ``GammaCase.frame_key`` lookup (``"default" if
    # is_default else label``) agrees with the original.
    is_default: bool = False


@dataclass(frozen=True)
class ParsedScope:
    """A structure (§8) -- every kind is now covered: ``"case"``, the two
    loop kinds (``"for-loop"``/``"while-loop"``), and the three frame-only
    families the Phase-1 model had flattened to a generic scope kind --
    ``"flat-sequence"``/``"stacked-sequence"``, ``"diagram-disable"``/
    ``"conditional-disable"``/``"type-specialization"``, and
    ``"event-structure"``.

    ``case`` and the frame-only families use ``selector`` (case only,
    ``None`` otherwise) + ``frames`` (frame headers + per-frame drives, empty
    for the frame-only families -- see ``_parse_labeled_frames``); a loop
    uses ``body`` directly (its single implicit body, §2) plus its own
    ``shift_registers``/``tunnels`` border constructs (§8), rendered as
    siblings of ``body``'s own items. The two shapes don't overlap in
    practice (a loop's ``frames`` is always empty; every other kind's
    ``body``/``shift_registers``/``tunnels`` are always empty) -- kept as one
    dataclass because all of them are "a structure with a kind and
    contents," not because the fields are meaningful together.
    """

    kind: str  # "case" | "for-loop" | "while-loop" | one of §8's frame-only kinds
    selector: str | None = None  # case only
    frames: tuple[ParsedFrame, ...] = ()  # case + frame-only families
    body: tuple[ParsedBodyItem, ...] = ()  # loop only
    shift_registers: tuple[ParsedShiftRegister, ...] = ()  # loop only
    tunnels: tuple[ParsedTunnel, ...] = ()  # loop only
    # Phase 4: the header's own OPTIONAL trailing ``(id <uid>)`` annotation
    # (``_LVNET_SCOPE_ID_PREFIX``, verbose-only) -- the structure's real BD
    # uid, recovered by ``_split_scope_header_id`` BEFORE this scope's own
    # header-matching logic ever sees the header content (so every existing
    # ``content == ...``/``content.startswith(...)`` check below keeps
    # working against the header with its id annotation already stripped).
    # ``None`` for a header carrying no such annotation (terse text, or
    # lvnet text rendered before this pass existed) -- ``lvnet_reconstruct.
    # py`` falls back to a net-derived or freshly-minted uid in that case.
    uid: str | None = None


ParsedBodyItem = (
    ParsedNode | ParsedScope | ParsedFeedback | ParsedConstant | ParsedLocalVariable
)


@dataclass(frozen=True)
class ParsedDependencyTerminal:
    """One parsed inline connector-pane terminal under a ``uses :`` ``subVI``
    entry (lvnet §7a, verbose-only): ``(name, type, direction)`` only -- no
    requirement keyword, no driver/default (``_render_lvnet_dependency_
    interface`` never emits either: this is the dependency's SIGNATURE, not
    a call site's own wiring)."""

    name: str
    type: str
    direction: str  # "in" | "out"


@dataclass(frozen=True)
class ParsedDependency:
    """One parsed ``uses :`` entry (new §2/§7 note) -- the exact fields
    ``NetlistDependency`` renders: the §7 kind keyword (``subVI``/
    ``typedef``/``class``), the fully-qualified identity, the optional
    ``; ./path`` nav annotation (``None`` when the line carried none), and
    (§7a, verbose-only) the ordered inline connector-pane interface a
    ``subVI`` entry may carry right under its own line -- ``()`` for a
    ``class``/``typedef`` entry, an unresolved ``subVI`` dependency, or
    terse mode (``_render_lvnet_uses`` never emits the block there)."""

    kind: str
    qualified: str
    path: str | None = None
    interface: tuple[ParsedDependencyTerminal, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ParsedTypeDef:
    """One ``types :`` footnote entry (§10): the lossless structural
    ``def_text`` (``Enum{...}``/``Cluster{...}``) and, when the entry carried
    a ``; ./path`` nav suffix, the ``path`` from it (the ``./`` prefix
    removed) -- kept so ``reconstruct_module`` can restore
    ``LVType.typedef_path`` and re-render the exact ``; ./path``."""

    def_text: str
    path: str | None = None


@dataclass(frozen=True)
class ParsedLvnet:
    """The result of parsing an lvnet text: header, the OPTIONAL ``uses :``
    dependency manifest, the Phase 2 ``front-panel :`` section (pattern +
    boundary block), the ``block-diagram :`` body + its trailing
    boundary-output-drive lines, and the OPTIONAL bottom-appendix
    ``types :`` footnote section (§10, verbose-only).

    ``pattern_id`` is ``ConnectorPane.pattern_id`` (the conId) -- ``None``
    when the ``front-panel :`` section carried no ``pattern :`` line (an
    unknown pattern, or no ``front-panel :`` section at all).

    ``types`` maps each NAMED type to its ``ParsedTypeDef`` (the structural
    ``def_text`` plus the optional ``; ./path``) -- ``netlist_signature``'s
    strengthened type comparison resolves a bare by-name type reference
    through this dict (``_parsed_type_ref_shape``), recursively, to compare
    full structure against the module side instead of by-name-vs-by-name;
    ``reconstruct_module`` also reads each entry's ``path``.
    """

    vi_name: str
    uses: tuple[ParsedDependency, ...] = field(default_factory=tuple)
    pattern_id: int | None = None
    boundary: tuple[ParsedBoundaryTerminal, ...] = field(default_factory=tuple)
    body: tuple[ParsedBodyItem, ...] = field(default_factory=tuple)
    output_drives: tuple[ParsedDrive, ...] = field(default_factory=tuple)
    types: dict[str, ParsedTypeDef] = field(default_factory=dict)


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
    prefix = f"{keyword}{_LVNET_DRIVER_OP}"
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
        sep_idx = rest.find(_LVNET_TYPE_SEP)
        if sep_idx == -1:
            raise LvnetParseError(
                f"line {line_no}: missing ' : <Type>' clause (§3): {line!r}"
            )
        name = rest[:sep_idx].strip()
        tail = rest[sep_idx + len(_LVNET_TYPE_SEP) :]
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
    sep_idx = rest.find(_LVNET_TYPE_SEP)
    if sep_idx == -1:
        raise LvnetParseError(
            f"line {line_no}: node declaration missing ' : <component>' "
            f"(§7): {content!r}"
        )
    handle = rest[:sep_idx]
    component = rest[sep_idx + len(_LVNET_TYPE_SEP) :]
    if not handle or " " in handle:
        raise LvnetParseError(
            f"line {line_no}: node handle must be a single space-free "
            f"token (§7/§9): {content!r}"
        )
    terminals, has_todo = _parse_terminal_block(cursor, indent + _LVNET_INDENT_WIDTH)
    return ParsedNode(
        kind=kw,
        handle=handle,
        component=component,
        terminals=tuple(terminals),
        has_todo=has_todo,
    )


def _parse_local_variable(content: str, line_no: int) -> ParsedLocalVariable:
    """``local-variable <handle> : read`` / ``local-variable <handle> :
    write = <source>`` (§7, now designed) -- a single line, no cursor
    consumption (unlike ``_parse_node``, there is no following terminal
    block to read)."""
    rest = content[len("local-variable ") :]
    sep_idx = rest.find(_LVNET_TYPE_SEP)
    if sep_idx == -1:
        raise LvnetParseError(
            f"line {line_no}: local-variable declaration missing "
            f"' : read'/' : write = <source>' (§7): {content!r}"
        )
    handle = rest[:sep_idx]
    tail = rest[sep_idx + len(_LVNET_TYPE_SEP) :]
    if not handle or " " in handle:
        raise LvnetParseError(
            f"line {line_no}: local-variable handle must be a single "
            f"space-free token (§7/§9): {content!r}"
        )
    if tail == "read":
        return ParsedLocalVariable(handle=handle, is_write=False)
    write_prefix = f"write{_LVNET_DRIVER_OP}"
    if not tail.startswith(write_prefix):
        raise LvnetParseError(
            f"line {line_no}: local-variable must be ' : read' or "
            f"' : write = <source>' (§7): {content!r}"
        )
    source = tail[len(write_prefix) :]
    if not source:
        raise LvnetParseError(
            f"line {line_no}: local-variable write has an empty source: "
            f"{content!r}"
        )
    return ParsedLocalVariable(handle=handle, is_write=True, source=source)


def _parse_constant_line(content: str, line_no: int) -> ParsedConstant:
    """``constant <handle> : <Type> = <value>`` (§7). ``tail.find(" = ")``
    takes the FIRST ``" = "`` -- safe without any quote-awareness, since a
    ``<Type>`` clause never contains that substring, so the first match is
    always the real operator regardless of what the (possibly quoted,
    md §4/§10) value afterward contains."""
    rest = content[len("constant ") :]
    sep_idx = rest.find(_LVNET_TYPE_SEP)
    if sep_idx == -1:
        raise LvnetParseError(
            f"line {line_no}: constant line missing ' : <Type>' (§7): {content!r}"
        )
    handle = rest[:sep_idx]
    tail = rest[sep_idx + len(_LVNET_TYPE_SEP) :]
    eq_idx = tail.find(_LVNET_DRIVER_OP)
    if eq_idx == -1:
        raise LvnetParseError(
            f"line {line_no}: constant line missing ' = <value>' (§7): {content!r}"
        )
    value = tail[eq_idx + len(_LVNET_DRIVER_OP) :]
    _validate_if_quoted(value, line_no)
    return ParsedConstant(handle=handle, type=tail[:eq_idx], value=value)


def _parse_feedback(
    cursor: _Cursor, indent: int, content: str, line_no: int
) -> ParsedFeedback:
    rest = content[len("feedback-node ") :]
    if not rest.endswith(_LVNET_BLOCK_OPEN):
        raise LvnetParseError(
            f"line {line_no}: feedback-node header must end with ' :' (§7): "
            f"{content!r}"
        )
    rest = rest[: -len(_LVNET_BLOCK_OPEN)]
    open_paren = rest.find(" (")
    if open_paren == -1 or not rest.endswith(")"):
        raise LvnetParseError(
            f"line {line_no}: feedback-node header missing "
            f"'(<N> iteration[s])' (§7): {content!r}"
        )
    net = rest[:open_paren]
    attribute = rest[open_paren + 2 : -1]
    child_indent = indent + _LVNET_INDENT_WIDTH
    init = _expect_kv_line(cursor, child_indent, "init", required=True)
    assert init is not None
    each = _expect_kv_line(cursor, child_indent, "each", required=False)
    return ParsedFeedback(net=net, attribute=attribute, init=init, each=each)


def _parse_shift_register(
    cursor: _Cursor, body_indent: int, content: str, line_no: int
) -> ParsedShiftRegister:
    rest = content[len("shift-register ") :]
    if not rest.endswith(_LVNET_BLOCK_OPEN):
        raise LvnetParseError(
            f"line {line_no}: shift-register header must end with ' :' "
            f"(§8): {content!r}"
        )
    net = rest[: -len(_LVNET_BLOCK_OPEN)]
    child_indent = body_indent + _LVNET_INDENT_WIDTH
    init = _expect_kv_line(cursor, child_indent, "init", required=True)
    assert init is not None
    each = _expect_kv_line(cursor, child_indent, "each", required=False)
    return ParsedShiftRegister(net=net, init=init, each=each)


def _parse_tunnel(content: str, line_no: int) -> ParsedTunnel:
    rest = content[len("tunnel ") :]
    sep_idx = rest.find(_LVNET_TYPE_SEP)
    if sep_idx == -1:
        raise LvnetParseError(
            f"line {line_no}: tunnel line missing ' : <mode>' (§8): {content!r}"
        )
    net = rest[:sep_idx]
    tail = rest[sep_idx + len(_LVNET_TYPE_SEP) :]
    eq_idx = tail.find(_LVNET_DRIVER_OP)
    if eq_idx == -1:
        raise LvnetParseError(
            f"line {line_no}: tunnel line missing ' = <source>' (§8): {content!r}"
        )
    return ParsedTunnel(
        net=net, mode=tail[:eq_idx], source=tail[eq_idx + len(_LVNET_DRIVER_OP) :]
    )


def _split_scope_header_id(content: str) -> tuple[str, str | None]:
    """Split OFF an OPTIONAL trailing `` (id <uid>)`` structure-identity
    annotation (§8, Phase 4, verbose-only) from a scope-header line's
    content, immediately before its block-opening `` :`` -- the exact
    reverse of ``render_lvnet._lvnet_scope_id_suffix``. Returns the REDUCED
    content, as if the annotation had never been there (so every existing
    header-matching branch in ``_parse_one_item_or_drive`` -- and every
    kind-specific parser's own header slicing below it -- keeps working
    completely unchanged), plus the recovered uid (``None`` when the line
    carries no such annotation: not a parse error, just a header rendered
    terse, or by a build of ``render_lvnet`` that predates this pass).

    A false-positive strip is not a realistic risk: every real scope header
    this could ever be called on is either a fixed keyword (``for-loop
    :``/``while-loop :``/the frame-only families' own headers) or a ``case
    <selector> :`` line whose selector is always a single space-free net
    token (§9) -- never itself containing literal `` (id <digits>)`` text.
    """
    if not content.endswith(_LVNET_BLOCK_OPEN):
        return content, None
    body = content[: -len(_LVNET_BLOCK_OPEN)]
    if not body.endswith(")"):
        return content, None
    open_idx = body.rfind(_LVNET_SCOPE_ID_PREFIX)
    if open_idx == -1:
        return content, None
    uid = body[open_idx + len(_LVNET_SCOPE_ID_PREFIX) : -1]
    if not uid.isdigit():
        return content, None
    return body[:open_idx] + _LVNET_BLOCK_OPEN, uid


def _parse_loop_scope(
    cursor: _Cursor, indent: int, content: str, line_no: int, uid: str | None
) -> ParsedScope:
    kind = "while-loop" if content == f"while-loop{_LVNET_BLOCK_OPEN}" else "for-loop"
    body_indent = indent + _LVNET_INDENT_WIDTH
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
        uid=uid,
    )


_LVNET_CASE_DEFAULT_SUFFIX = f", {_LVNET_DEFAULT_KEYWORD}"


def _parse_case_frame_header(
    header: str, frame_line_no: int, frame_line: str
) -> tuple[str, bool]:
    """Split one case-frame header's post-``frame ``, pre-``` :``` text into
    ``(label, is_default)`` (§8's ``"Error", default`` convention --
    ``_render_lvnet_case_scope``'s exact inverse).

    Three shapes, in order:
    - the bare keyword ``default`` (no quotes at all) -- a pure default frame
      with no specific selector value; recovered as the ``"Default"``
      sentinel label (matching ``_selector_label``'s own non-error-default
      text, so re-rendering hits the SAME bare-keyword branch again).
    - one or more double-quoted value tokens (kept WITH their quotes,
      verbatim -- same passthrough convention ``_quoted_frame_label`` already
      relies on) followed by a literal ``, default`` suffix -- a frame that
      catches a specific value AND is the default (the Error-cluster case:
      ``_selector_label``'s ``is_error`` branch never returns the ``Default``
      sentinel, so its default frame keeps a real value).
    - the quoted value token(s) alone, no suffix -- a plain, non-default
      frame (unchanged from before this feature).

    The quoted portion is scanned quote-literal-at-a-time (honoring
    ``\\``-escapes via ``_scan_quoted_literal``) rather than by a naive
    string-suffix check, so a string selector's OWN text can legitimately
    contain the substring ``", default"`` (e.g. a value literally reading
    ``"foo, default"``) without being mistaken for the keyword suffix.
    """
    if header == _LVNET_DEFAULT_KEYWORD:
        return "Default", True
    if not header.startswith('"'):
        raise LvnetParseError(
            f"line {frame_line_no}: expected 'frame \"<value>\"[, default] :' "
            f"or 'frame default :' inside a case scope (§8), got {frame_line!r}"
        )
    end = _scan_quoted_literal(header, 0)
    n = len(header)
    while (
        end + 2 < n
        and header[end : end + 2] == ", "
        and header[end + 2] == '"'
    ):
        end = _scan_quoted_literal(header, end + 2)
    value_part = header[:end]
    rest = header[end:]
    if rest == "":
        return value_part, False
    if rest == _LVNET_CASE_DEFAULT_SUFFIX:
        return value_part, True
    raise LvnetParseError(
        f"line {frame_line_no}: unexpected trailing text {rest!r} after case "
        f"frame value (§8), got {frame_line!r}"
    )


def _parse_case_scope(
    cursor: _Cursor, indent: int, content: str, line_no: int, uid: str | None
) -> ParsedScope:
    selector = content[len("case ") : -len(_LVNET_BLOCK_OPEN)]
    frame_indent = indent + _LVNET_INDENT_WIDTH
    body_indent = indent + _LVNET_INDENT_WIDTH * 2
    frames: list[ParsedFrame] = []
    while True:
        line = cursor.peek()
        if line is None or line.strip() == "" or _indent_len(line) != frame_indent:
            break
        frame_line_no = cursor.line_no
        frame_line = cursor.take()
        frame_content = frame_line[frame_indent:]
        if not (
            frame_content.startswith("frame ")
            and frame_content.endswith(_LVNET_BLOCK_OPEN)
        ):
            raise LvnetParseError(
                f"line {frame_line_no}: expected 'frame \"<value>\"[, default] :' "
                f"or 'frame default :' inside a case scope (§8), got {frame_line!r}"
            )
        header = frame_content[len("frame ") : -len(_LVNET_BLOCK_OPEN)]
        label, is_default = _parse_case_frame_header(header, frame_line_no, frame_line)
        items, drives = _parse_items(cursor, body_indent)
        frames.append(
            ParsedFrame(
                label=label,
                body=tuple(items),
                drives=tuple(drives),
                is_default=is_default,
            )
        )
    if not frames:
        raise LvnetParseError(
            f"line {line_no}: case scope has no frames (§8): {content!r}"
        )
    return ParsedScope(kind="case", selector=selector, frames=tuple(frames), uid=uid)


def _parse_labeled_frames(
    cursor: _Cursor, frame_indent: int, body_indent: int
) -> list[ParsedFrame]:
    """Parse a run of ``frame <label> :`` blocks (§8) at EXACTLY
    ``frame_indent`` spaces -- shared by the sequence/disabled/event-
    structure families (``_parse_sequence_scope``/``_parse_disabled_scope``/
    ``_parse_event_scope``), unlike ``_parse_case_scope`` which requires its
    label to be quoted: these families render some labels bare (``[0]``,
    ``Enabled``) and some quoted (a Conditional Disable symbol condition, an
    event label) -- so ``label`` is kept VERBATIM, quotes and all, exactly as
    ``_parse_case_scope`` already does for its own (always-quoted) labels.

    None of these three families' ``NetlistScope``s carry any output MERGE
    (see ``NetlistScope.outputs``'s docstring: "empty for every other scope
    kind" -- sequence/disabled/event have none), so unlike a case frame, a
    frame here never has its own ``net = source`` drive lines; encountering
    one is a genuine grammar violation for this scope family, raised rather
    than silently accepted.
    """
    frames: list[ParsedFrame] = []
    while True:
        line = cursor.peek()
        if line is None or line.strip() == "" or _indent_len(line) != frame_indent:
            break
        frame_line_no = cursor.line_no
        frame_line = cursor.take()
        frame_content = frame_line[frame_indent:]
        if not (
            frame_content.startswith("frame ")
            and frame_content.endswith(_LVNET_BLOCK_OPEN)
        ):
            raise LvnetParseError(
                f"line {frame_line_no}: expected 'frame <label> :' (§8), "
                f"got {frame_line!r}"
            )
        label = frame_content[len("frame ") : -len(_LVNET_BLOCK_OPEN)]
        items, drives = _parse_items(cursor, body_indent)
        if drives:
            raise LvnetParseError(
                f"line {frame_line_no}: this scope family has no output "
                f"merge to drive, so its frames must not contain bare "
                f"'net = source' lines (§8): {drives!r}"
            )
        frames.append(ParsedFrame(label=label, body=tuple(items), drives=()))
    return frames


def _parse_sequence_scope(
    cursor: _Cursor, indent: int, content: str, line_no: int, uid: str | None
) -> ParsedScope:
    """``flat-sequence :`` / ``stacked-sequence :`` (§8) -- ``frame [i] :``
    per frame, matching ``_render_lvnet_sequence_scope`` exactly."""
    kind = "flat-sequence" if content == "flat-sequence :" else "stacked-sequence"
    frames = _parse_labeled_frames(
        cursor, indent + _LVNET_INDENT_WIDTH, indent + _LVNET_INDENT_WIDTH * 2
    )
    if not frames:
        raise LvnetParseError(
            f"line {line_no}: {kind} scope has no frames (§8): {content!r}"
        )
    return ParsedScope(kind=kind, frames=tuple(frames), uid=uid)


def _parse_disabled_scope(
    cursor: _Cursor, indent: int, content: str, line_no: int, uid: str | None
) -> ParsedScope:
    """``diagram-disable :`` / ``conditional-disable :`` / ``type-
    specialization :`` (§8), matching ``_render_lvnet_disabled_scope``
    exactly -- ``kind`` is the exact header word (minus its trailing
    ``" :"``), so ``netlist_signature`` compares it directly against
    ``_LVNET_DISABLE_KEYWORD[scope.disable_kind]`` with no extra mapping."""
    kind = content[: -len(_LVNET_BLOCK_OPEN)]
    frames = _parse_labeled_frames(
        cursor, indent + _LVNET_INDENT_WIDTH, indent + _LVNET_INDENT_WIDTH * 2
    )
    if not frames:
        raise LvnetParseError(
            f"line {line_no}: {kind} scope has no frames (§8): {content!r}"
        )
    return ParsedScope(kind=kind, frames=tuple(frames), uid=uid)


def _parse_event_scope(
    cursor: _Cursor, indent: int, content: str, line_no: int, uid: str | None
) -> ParsedScope:
    """``event-structure :`` (§8) -- ``frame "<event>" :`` per event case,
    matching ``_render_lvnet_event_scope`` exactly."""
    frames = _parse_labeled_frames(
        cursor, indent + _LVNET_INDENT_WIDTH, indent + _LVNET_INDENT_WIDTH * 2
    )
    if not frames:
        raise LvnetParseError(
            f"line {line_no}: event-structure scope has no frames (§8): "
            f"{content!r}"
        )
    return ParsedScope(kind="event-structure", frames=tuple(frames), uid=uid)


def _parse_one_item_or_drive(
    cursor: _Cursor, indent: int, content: str, line_no: int
) -> ParsedBodyItem | ParsedDrive:
    if content.startswith("local-variable "):
        return _parse_local_variable(content, line_no)
    if content.startswith("constant "):
        return _parse_constant_line(content, line_no)
    if content.startswith("feedback-node "):
        return _parse_feedback(cursor, indent, content, line_no)
    for kw in _NODE_KEYWORDS:
        if content.startswith(kw + " "):
            return _parse_node(cursor, indent, kw, content, line_no)
    # Strip an optional Phase-4 `` (id <uid>)`` header annotation BEFORE any
    # of the scope-header matching below -- every one of these checks (an
    # exact-set membership, a `startswith`) was written against the header
    # AS IF that annotation never existed, and stays correct unchanged once
    # it's peeled off here (see ``_split_scope_header_id``'s own docstring).
    scope_content, scope_uid = _split_scope_header_id(content)
    if scope_content in (
        f"for-loop{_LVNET_BLOCK_OPEN}",
        f"while-loop{_LVNET_BLOCK_OPEN}",
    ):
        return _parse_loop_scope(cursor, indent, scope_content, line_no, scope_uid)
    if scope_content.startswith("case ") and scope_content.endswith(_LVNET_BLOCK_OPEN):
        return _parse_case_scope(cursor, indent, scope_content, line_no, scope_uid)
    if scope_content in _SEQUENCE_SCOPE_HEADERS:
        return _parse_sequence_scope(cursor, indent, scope_content, line_no, scope_uid)
    if scope_content in _DISABLED_SCOPE_HEADERS:
        return _parse_disabled_scope(cursor, indent, scope_content, line_no, scope_uid)
    if scope_content == _EVENT_SCOPE_HEADER:
        return _parse_event_scope(cursor, indent, scope_content, line_no, scope_uid)
    if _LVNET_DRIVER_OP in content:
        # FIRST occurrence: a net name (``case_UID::outK``/``loop_UID::
        # shiftK``/a boundary control's name) never contains " = ", so it's
        # always the real operator regardless of what a (possibly quoted,
        # md §4/§10) literal source afterward contains.
        net, _, source = content.partition(_LVNET_DRIVER_OP)
        _validate_if_quoted(source, line_no)
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

# Indent of a ``uses :`` entry's inline §7a interface lines -- one level
# (2 spaces) deeper than the entry's own 4-space indent, matching
# ``netlist._LVNET_DEP_INTERFACE_INDENT``.
_USES_INTERFACE_INDENT = 6


def _parse_dependency_interface(
    cursor: _Cursor, indent: int
) -> tuple[ParsedDependencyTerminal, ...]:
    """Read a ``subVI`` ``uses :`` entry's inline connector-pane interface
    (lvnet §7a, verbose-only), immediately following that entry's own line,
    at EXACTLY ``indent`` spaces -- the SAME ``in ``/``out`` terminal-line
    shape as a boundary line (§3), minus the §5 requirement keyword and §4
    default clause (``_render_lvnet_dependency_interface`` never emits
    either). Stops at the first line that isn't one: a dedent back to the
    next ``uses :`` entry (4-space) or the boundary block (2-space), or the
    end of the manifest. Absent entirely (``()``) for a ``class``/``typedef``
    entry or an unresolved ``subVI`` dependency -- ``render_lvnet`` never
    emits the block in either case, so its absence here is never itself an
    error.
    """
    terminals: list[ParsedDependencyTerminal] = []
    while True:
        line = cursor.peek()
        if line is None or line.strip() == "" or _indent_len(line) != indent:
            break
        content = line[indent:]
        m = _TERMINAL_CONTENT_RE.match(content)
        if m is None:
            raise LvnetParseError(
                f"line {cursor.line_no}: expected an 'in '/'out' dependency "
                f"interface line (§7a), got {line!r}"
            )
        line_no = cursor.line_no
        cursor.take()
        direction, rest = m.group(1), m.group(2)
        sep_idx = rest.find(_LVNET_TYPE_SEP)
        if sep_idx == -1:
            raise LvnetParseError(
                f"line {line_no}: missing ' : <Type>' clause in dependency "
                f"interface line (§3/§7a): {line!r}"
            )
        name = rest[:sep_idx].strip()
        type_str = rest[sep_idx + len(_LVNET_TYPE_SEP) :]
        if not name:
            raise LvnetParseError(
                f"line {line_no}: empty terminal name in dependency "
                f"interface line: {line!r}"
            )
        terminals.append(
            ParsedDependencyTerminal(name=name, type=type_str, direction=direction)
        )
    return tuple(terminals)


def _parse_uses_block(cursor: _Cursor) -> tuple[ParsedDependency, ...]:
    """Parse the OPTIONAL ``uses :`` dependency manifest (new §2/§7 note) --
    immediately after the ``vi <name> :`` header, before the boundary block.
    Absent entirely (``()``) when the next line isn't exactly the ``uses :``
    header -- ``render_lvnet`` omits the section when the VI has no
    dependencies, rather than emit an empty header, so its absence is never
    itself an error.

    Once inside a confirmed ``uses :`` block, every entry line is expected at
    EXACTLY 4-space indent (one level deeper than the header's 2 spaces) --
    the boundary block's own ``in ``/``out`` lines are always 2-space (never
    4-space), so indent alone unambiguously ends this block with no blank-line
    separator needed (matching ``render_lvnet``'s own header-to-boundary
    style, which has none either).
    """
    if cursor.peek() != _USES_HEADER_LINE:
        return ()
    cursor.take()
    entries: list[ParsedDependency] = []
    while True:
        line = cursor.peek()
        if line is None or _indent_len(line) != 4:
            break
        line_no = cursor.line_no
        cursor.take()
        m = _USES_ENTRY_RE.match(line)
        if m is None:
            raise LvnetParseError(
                f"line {line_no}: expected a 'uses :' dependency entry, got {line!r}"
            )
        kind, rest = m.group(1), m.group(2)
        if kind not in _USES_KIND_WORDS:
            raise LvnetParseError(
                f"line {line_no}: unrecognized 'uses :' kind {kind!r} "
                f"(expected one of {sorted(_USES_KIND_WORDS)}): {line!r}"
            )
        sep_idx = rest.find(_LVNET_DEP_PATH_SEP)
        if sep_idx == -1:
            qualified, path = rest.strip(), None
        else:
            qualified, path = (
                rest[:sep_idx].rstrip(),
                rest[sep_idx + len(_LVNET_DEP_PATH_SEP) :],
            )
        if not qualified:
            raise LvnetParseError(
                f"line {line_no}: empty qualified identity in 'uses :' entry: {line!r}"
            )
        interface = _parse_dependency_interface(cursor, _USES_INTERFACE_INDENT)
        entries.append(
            ParsedDependency(
                kind=kind, qualified=qualified, path=path, interface=interface
            )
        )
    if not entries:
        raise LvnetParseError(
            "'uses :' header present but the block has no dependency entries"
        )
    return tuple(entries)


def _parse_boundary_terminal_line(
    content: str, line_no: int, line: str
) -> ParsedBoundaryTerminal:
    """Parse one already indent-stripped ``in ``/``out`` boundary CONTENT
    line (§3, applied to the connector pane) -- shared by
    ``_parse_front_panel_block``."""
    m = _TERMINAL_CONTENT_RE.match(content)
    if m is None:
        raise LvnetParseError(
            f"line {line_no}: expected an 'in '/'out' boundary terminal "
            f"line, got {line!r}"
        )
    direction, rest = m.group(1), m.group(2)
    # Split on the STRUCTURAL ``" : "`` (space-colon-space) token, never
    # a bare ``":"``: a terminal's own authored name can itself contain
    # a literal colon with no preceding space (a real corpus control
    # named ``"txtRuns:"``), which a first-bare-":" split mis-splits.
    sep_idx = rest.find(_LVNET_TYPE_SEP)
    if sep_idx == -1:
        raise LvnetParseError(
            f"line {line_no}: missing ' : <Type>' clause (§3): {line!r}"
        )
    name = rest[:sep_idx].strip()
    tail = rest[sep_idx + len(_LVNET_TYPE_SEP) :]
    if not name:
        raise LvnetParseError(f"line {line_no}: empty terminal name: {line!r}")
    type_str, requirement, default, index = _split_type_requirement_default(
        tail, line_no, line
    )
    return ParsedBoundaryTerminal(
        name=name,
        type=type_str,
        direction=direction,
        requirement=requirement,
        default=default,
        index=index,
    )


def _parse_front_panel_block(
    cursor: _Cursor,
) -> tuple[int | None, list[ParsedBoundaryTerminal]]:
    """Parse the OPTIONAL Phase 2 ``front-panel :`` section (the VI's own
    connector pane): the ``pattern : <conId>`` line (OPTIONAL -- omitted by
    ``render_lvnet`` when the pattern is unknown) followed by the boundary
    ``in``/``out`` terminal block, both at 4-space indent (one level deeper
    than the section header's own 2 spaces). Absent entirely (``(None,
    [])``) when the next line isn't exactly the ``front-panel :`` header --
    ``render_lvnet`` omits the section when there is nothing to show, rather
    than emit an empty header, so its absence here is never itself an
    error.
    """
    if cursor.peek() != _FRONT_PANEL_HEADER_LINE:
        return None, []
    cursor.take()
    content_indent = _LVNET_INDENT_WIDTH * 2

    pattern_id: int | None = None
    pattern_prefix = f"{_LVNET_PATTERN_KEYWORD}{_LVNET_TYPE_SEP}"
    line = cursor.peek()
    if line is not None and _indent_len(line) == content_indent:
        content = line[content_indent:]
        if content.startswith(pattern_prefix):
            line_no = cursor.line_no
            cursor.take()
            pattern_text = content[len(pattern_prefix) :]
            try:
                pattern_id = int(pattern_text)
            except ValueError:
                raise LvnetParseError(
                    f"line {line_no}: 'pattern :' value must be an integer "
                    f"conId: {line!r}"
                ) from None

    boundary: list[ParsedBoundaryTerminal] = []
    while True:
        line = cursor.peek()
        if line is None or _indent_len(line) != content_indent:
            break
        line_no = cursor.line_no
        cursor.take()
        boundary.append(
            _parse_boundary_terminal_line(line[content_indent:], line_no, line)
        )
    return pattern_id, boundary


def _parse_block_diagram_block(
    cursor: _Cursor,
) -> tuple[list[ParsedBodyItem], list[ParsedDrive]]:
    """Parse the Phase 2 ``block-diagram :`` section (ALWAYS present,
    unlike ``front-panel :``/``uses :``/``types :`` -- every VI has a
    diagram, even an empty one): the VI's own body, one level deeper (4
    spaces) than the section header. The boundary-output-drive lines
    (§2's ``<out name> = <source-net>``) now live at the END of this SAME
    body, at the SAME indent as its own top-level items -- rendered with
    the plain generic ``net = source`` shape (``render_lvnet``'s own
    ``_LVNET_DRIVER_OP``), so they parse through the exact same generic
    bare-drive path ``_parse_items``/``_parse_one_item_or_drive`` already
    uses for a case frame's own drives; no dedicated output-drive grammar
    or "stray drives are an error" check is needed here any more.
    """
    line = cursor.peek()
    if line != _BLOCK_DIAGRAM_HEADER_LINE:
        raise LvnetParseError(
            f"line {cursor.line_no}: expected the 'block-diagram :' "
            f"section header, got {line!r}"
        )
    cursor.take()
    content_indent = _LVNET_INDENT_WIDTH * 2
    return _parse_items(cursor, content_indent)


def _parse_types_block(cursor: _Cursor) -> dict[str, ParsedTypeDef]:
    """Parse the OPTIONAL bottom-appendix ``types :`` footnote section
    (§10, verbose-only) -- immediately after the final boundary-output-
    drive block, at the very end of the document. Absent entirely (``{}``)
    when the next line isn't exactly the ``types :`` header --
    ``render_lvnet`` omits the section when the VI has no NAMED types,
    rather than emit an empty header, so its absence here is never itself
    an error.

    Each entry is ``<Name> = <def>[ ; ./path]`` at 4-space indent (one level
    deeper than the header's 2 spaces) -- the FIRST `` = `` occurrence is
    always the real name/def separator (a type's own display name never
    itself contains `` = ``, matching every other name/value split in this
    module), and the trailing `` ; ./path`` nav clause (if present) is
    stripped from the stored def text -- ``;`` never appears inside the
    lossless grammar itself (``_lvnet_type_lossless_def`` never emits one),
    so this split can never clip real structure.
    """
    if cursor.peek() != _TYPES_HEADER_LINE:
        return {}
    cursor.take()
    defs: dict[str, ParsedTypeDef] = {}
    while True:
        line = cursor.peek()
        if line is None or _indent_len(line) != 4:
            break
        line_no = cursor.line_no
        cursor.take()
        content = line[4:]
        eq_idx = content.find(_LVNET_DRIVER_OP)
        if eq_idx == -1:
            raise LvnetParseError(
                f"line {line_no}: expected a 'types :' entry "
                f"'<Name> = <def>' (§10), got {line!r}"
            )
        name = content[:eq_idx]
        def_text, _, path_suffix = content[eq_idx + len(_LVNET_DRIVER_OP) :].partition(
            _LVNET_ANNOTATION_SEP
        )
        # render emits the nav suffix as ``; ./{typedef_path}`` (netlist.py
        # _render_lvnet_types), so strip the leading ``./`` back off to recover
        # the raw typedef_path -- reconstruct_module re-adds ``./``.
        path = (
            path_suffix[len(_LVNET_TYPEDEF_NAV_PREFIX) :]
            if path_suffix.startswith(_LVNET_TYPEDEF_NAV_PREFIX)
            else (path_suffix or None)
        )
        if not name:
            raise LvnetParseError(
                f"line {line_no}: empty type name in 'types :' entry: {line!r}"
            )
        defs[name] = ParsedTypeDef(def_text=def_text, path=path)
    return defs


def parse_lvnet(text: str) -> ParsedLvnet:
    """Parse an lvnet text's ``vi <name> :`` header, OPTIONAL ``uses :``
    dependency manifest, OPTIONAL Phase 2 ``front-panel :`` section
    (pattern + boundary block), the ``block-diagram :`` BODY (with its
    trailing boundary-output-drive lines), and OPTIONAL bottom-appendix
    ``types :`` footnote section (§10). Raises ``LvnetParseError`` naming
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
    uses = _parse_uses_block(cursor)
    pattern_id, boundary = _parse_front_panel_block(cursor)
    body_items, output_drives = _parse_block_diagram_block(cursor)
    type_defs = _parse_types_block(cursor)

    return ParsedLvnet(
        vi_name=vi_name,
        uses=uses,
        pattern_id=pattern_id,
        boundary=tuple(boundary),
        body=tuple(body_items),
        output_drives=tuple(output_drives),
        types=type_defs,
    )


# ============================================================
# Signatures -- the canonical, comparable projections
# ============================================================

# The comparable projection of ONE type reference in a signature tuple --
# either a plain ``("leaf", <label string>)`` (unchanged, string-equality
# comparison) or a decomposed structural shape once a §10 NAMED type is
# reached (``("enum", members)`` / ``("cluster", fields)`` / ``("array",
# dims, inner)`` / ``("refnum", ref_type, inner)`` / ``("named", name)`` on
# a cycle) -- see ``netlist._lv_type_comparison_shape`` (MODULE side) and
# ``_parsed_type_ref_shape`` below (PARSED side), which build the identical
# shape independently so the two compare directly.
TypeShape = tuple[Any, ...]


def _split_top_level_commas(text: str) -> list[str]:
    """Split ``text`` on ``", "`` at brace/bracket DEPTH ZERO -- separates
    an ``Enum{...}``'s members or a ``Cluster{...}``'s fields (§10's
    lossless ``types :`` grammar) without breaking on a comma that's itself
    inside a NESTED structural type (a cluster field whose own type is
    another ``Cluster{...}``/array). No quote-awareness needed -- the
    lossless grammar carries pure type syntax, never a string-literal
    value."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in text:
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_lossless_members(body: str) -> tuple[tuple[str, int], ...]:
    """Parse an ``Enum{...}``/``Ring{...}`` footnote body (§10) into
    ``(member_name, ordinal)`` pairs, sorted by ordinal -- the SAME
    canonical order ``netlist._lv_type_comparison_shape`` builds on the
    module side. ``body`` is the text BETWEEN the outer braces."""
    body = body.strip()
    if not body or body == "?":
        return ()
    members: list[tuple[str, int]] = []
    for part in _split_top_level_commas(body):
        name, _, ordinal_text = part.rpartition(_LVNET_DRIVER_OP)
        members.append((name.strip(), int(ordinal_text.strip())))
    return tuple(sorted(members, key=lambda kv: kv[1]))


def _parse_lossless_cluster_fields(
    body: str,
    types_dict: dict[str, ParsedTypeDef],
    seen: frozenset[str],
    ambiguous: frozenset[str],
) -> tuple[tuple[str, TypeShape], ...] | None:
    """Parse a ``Cluster{...}`` footnote body (§10) into ``(field_name,
    resolved_type_shape)`` pairs -- ``None`` for the honest ``Cluster{ ? }``
    placeholder (fields never loaded), mirroring the module side's
    ``("cluster", None)`` shape for the same case."""
    body = body.strip()
    if body == "?":
        return None
    if not body:
        return ()
    fields: list[tuple[str, TypeShape]] = []
    for part in _split_top_level_commas(body):
        name, _sep, type_text = part.partition(_LVNET_TYPE_SEP)
        fields.append(
            (
                name.strip(),
                _parsed_type_ref_shape(
                    type_text.strip(), types_dict, seen, full=True, ambiguous=ambiguous
                ),
            )
        )
    return tuple(fields)


def _parsed_type_ref_shape(
    text: str,
    types_dict: dict[str, ParsedTypeDef],
    seen: frozenset[str] = frozenset(),
    *,
    full: bool = False,
    ambiguous: frozenset[str] = frozenset(),
) -> TypeShape:
    """The PARSED-side counterpart to ``netlist._lv_type_comparison_shape``
    -- built from TEXT (an already-flattened inline type label, OR a
    ``types :`` footnote's own def text) instead of a real ``LVType``, but
    producing the IDENTICAL tuple shape, so the two sides compare directly.
    See that function's docstring for the full ``full``/``seen``/
    ``ambiguous`` contract -- mirrored here exactly (``ambiguous`` --
    ``netlist._lvnet_ambiguous_named_types``, computed from the MODULE and
    passed down by the caller when both sides are being compared against
    each other -- treats a name known to resolve to more than one distinct
    structure elsewhere in the module as unnamed, falling back to a bare
    leaf instead of resolving through ``types_dict``, since the flat
    one-entry-per-name footnote can't be trusted for it).

    Recognizes, in order: an array wrapper (``[...]``, one or more dims);
    a refnum wrapper (``<ref_type> refnum{...}`` / ``<ref_type> refnum`` /
    bare ``refnum``); the NEW capitalized lossless-grammar structural forms
    (``Enum{...}``/``Ring{...}``/``Cluster{...}`` -- these NEVER appear in
    the OLD terse/anonymous inline label, only inside a resolved footnote's
    own def text, so there's no ambiguity with an anonymous cluster's
    lowercase ``cluster{...}`` label); and finally a bare NAME that
    resolves through ``types_dict`` (recursively, cycle-guarded by
    ``seen``, skipped when the name is in ``ambiguous``). Anything else (a
    scalar token, a qualified class name, the OLD lowercase
    ``cluster{...}``/``enum{...}``/``ring{...}`` anonymous label,
    ``"Error"``, an unresolved bare name) is an opaque ``("leaf", text)``
    -- compared by plain string equality, exactly as before this pass.
    """
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        dims = 0
        inner = text
        while inner.startswith("[") and inner.endswith("]"):
            inner = inner[1:-1]
            dims += 1
        return (
            "array",
            dims,
            _parsed_type_ref_shape(
                inner.strip(), types_dict, seen, full=full, ambiguous=ambiguous
            ),
        )
    refnum_idx = text.find(" refnum{")
    if refnum_idx != -1 and text.endswith("}"):
        ref_type = text[:refnum_idx]
        inner = text[refnum_idx + len(" refnum{") : -1].strip()
        return (
            "refnum",
            ref_type,
            _parsed_type_ref_shape(
                inner, types_dict, seen, full=full, ambiguous=ambiguous
            ),
        )
    if text.endswith(" refnum") and "{" not in text:
        return ("leaf", text)
    if text.startswith(_LVNET_ENUM_OPEN) and text.endswith("}"):
        return ("enum", _parse_lossless_members(text[len(_LVNET_ENUM_OPEN) : -1]))
    if text.startswith(_LVNET_RING_OPEN) and text.endswith("}"):
        return ("ring", _parse_lossless_members(text[len(_LVNET_RING_OPEN) : -1]))
    if text.startswith(_LVNET_CLUSTER_OPEN) and text.endswith("}"):
        fields = _parse_lossless_cluster_fields(
            text[len(_LVNET_CLUSTER_OPEN) : -1], types_dict, seen, ambiguous
        )
        return ("cluster", fields)
    if text in types_dict and text not in ambiguous:
        if text in seen:
            return ("named", text)
        return _parsed_type_ref_shape(
            types_dict[text].def_text,
            types_dict,
            seen | {text},
            full=True,
            ambiguous=ambiguous,
        )
    return ("leaf", text)


def _module_default_token(default: ScalarValue) -> str | None:
    """The comparable ``default`` token for a MODULE-side BOUNDARY terminal
    (``ConnectorPaneTerminal.default``, a raw ``ScalarValue``) -- ``None``
    when the pane genuinely has no default recorded, else the exact literal
    text ``render_lvnet`` now emits for it (Gap #1, closed: see
    ``_lvnet_literal_token`` in ``netlist.py``)."""
    if default is None:
        return None
    return _lvnet_literal_token(default)


def boundary_signature(
    module_or_parsed: NetlistModule | ParsedLvnet,
    parsed_types: dict[str, ParsedTypeDef] | None = None,
    ambiguous: frozenset[str] = frozenset(),
) -> tuple[tuple[str, TypeShape, str, str | None, str | None, int | None], ...]:
    """The canonical, comparable boundary projection: an ordered tuple of
    ``(name, type_shape, direction, wiring_requirement_or_None,
    default_token_or_None, pane_index_or_None)`` per terminal (inputs then
    outputs) -- computed IDENTICALLY (same helper calls, same field
    sources) from a real ``NetlistModule`` and from a ``ParsedLvnet``.

    ``pane_index`` (Phase 2) is the connector-pane slot index
    (``ConnectorPaneTerminal.index`` / ``ParsedBoundaryTerminal.index``) --
    included so a round-trip proves the ``@<index>`` column itself
    survives, not just the terminal's name/type/requirement/default.

    ``type_shape`` is the STRENGTHENED §10 type comparison
    (``netlist._lv_type_comparison_shape`` / ``_parsed_type_ref_shape`` --
    a plain ``("leaf", label)`` unless a NAMED type is reached, in which
    case it's the full resolved structure). ``parsed_types`` is the
    ``ParsedLvnet.types`` footnote dict, needed ONLY to resolve the
    ``ParsedLvnet`` branch's by-name references -- unused (module side
    resolves directly from the real ``LVType`` objects it already holds).
    ``ambiguous`` -- see ``netlist._lvnet_ambiguous_named_types`` -- names
    excluded from the strengthened resolution on BOTH branches.
    """
    if isinstance(module_or_parsed, ParsedLvnet):
        types_dict = parsed_types if parsed_types is not None else {}
        return tuple(
            (
                t.name,
                _parsed_type_ref_shape(t.type, types_dict, ambiguous=ambiguous),
                t.direction,
                t.requirement,
                t.default,
                t.index,
            )
            for t in module_or_parsed.boundary
        )

    module = module_or_parsed
    pane_terminals = module.connector_pane.terminals
    n_in = len(module.inputs)
    n_out = len(module.outputs)
    input_panes: list[ConnectorPaneTerminal] = pane_terminals[:n_in]
    output_panes: list[ConnectorPaneTerminal] = pane_terminals[n_in : n_in + n_out]
    # Everything after the on-pane inputs/outputs is an OFF-PANE front-panel
    # control/indicator (``ConnectorPaneTerminal.index is None`` --
    # ``netlist_build._off_pane_terminals``) -- it has no ``module.inputs``/
    # ``.outputs`` counterpart at all (never drives/reads the VI's own
    # boundary), so its ``(name, type_shape, direction, requirement,
    # default, index)`` tuple is built straight from the pane terminal
    # itself, matching the PARSED branch above (which reads every boundary
    # line, on- or off-pane, uniformly from ``ParsedLvnet.boundary``).
    off_panes = pane_terminals[n_in + n_out :]

    entries: list[tuple[str, TypeShape, str, str | None, str | None, int | None]] = [
        (
            inp.name,
            _lv_type_comparison_shape(inp.lv_type, ambiguous=ambiguous)
            if inp.lv_type is not None
            else ("leaf", inp.type_descriptor),
            "in",
            _lvnet_requirement_trailing(pane),
            _module_default_token(pane.default),
            pane.index,
        )
        for inp, pane in zip(module.inputs, input_panes, strict=True)
    ] + [
        (
            o.name,
            _lv_type_comparison_shape(o.lv_type, ambiguous=ambiguous)
            if o.lv_type is not None
            else ("leaf", o.type_descriptor),
            "out",
            _lvnet_requirement_trailing(pane),
            _module_default_token(pane.default),
            pane.index,
        )
        for o, pane in zip(module.outputs, output_panes, strict=True)
    ] + [
        (
            pane.name,
            _lv_type_comparison_shape(pane.lv_type, ambiguous=ambiguous)
            if pane.lv_type is not None
            else ("leaf", pane.type),
            "in" if pane.direction == "input" else "out",
            _lvnet_requirement_trailing(pane),
            _module_default_token(pane.default),
            pane.index,
        )
        for pane in off_panes
    ]
    return tuple(entries)


def _dependency_interface_signature(
    interface: list[ConnectorPaneTerminal], ambiguous: frozenset[str]
) -> tuple[tuple[str, str, TypeShape], ...]:
    """The comparable projection of a ``uses :`` ``subVI`` entry's inline
    §7a interface: ``(direction, name, type_shape)`` per terminal,
    ``"input"``/``"output"`` normalized to ``"in"``/``"out"`` the same way
    ``boundary_signature`` normalizes a boundary terminal's direction, and
    the type computed through the SAME strengthened §10 shape
    (``netlist._lv_type_comparison_shape``) ``boundary_signature`` uses.
    """
    return tuple(
        (
            "in" if t.direction == "input" else "out",
            t.name,
            _lv_type_comparison_shape(t.lv_type, ambiguous=ambiguous)
            if t.lv_type is not None
            else ("leaf", t.type),
        )
        for t in interface
    )


def _strip_default_prefix(rendered: str) -> str:
    """``_lvnet_default_trailing``'s rendered text (``"default"`` or
    ``"default <literal>"``) -> just the value-side token this parser's
    ``ParsedTerminalLine.default`` carries (``"default"`` or ``"<literal>"``
    -- the word itself is not repeated, matching how the boundary side
    already represents this)."""
    if rendered == _LVNET_DEFAULT_KEYWORD:
        return rendered
    return rendered[len(_LVNET_DEFAULT_KEYWORD) + 1 :]


def _module_terminal_tuple(
    instance: NetlistInstance, handles: _LvnetHandles, ambiguous: frozenset[str]
) -> tuple[tuple[str, str, TypeShape, str | None, str | None, bool], ...]:
    entries: list[tuple[str, str, TypeShape, str | None, str | None, bool]] = []
    for b in sorted(instance.inputs, key=lambda b: b.pane_rank):
        if _is_void_type(b.type):
            continue
        type_shape = (
            _lv_type_comparison_shape(b.lv_type, ambiguous=ambiguous)
            if b.lv_type is not None
            else ("leaf", b.type)
        )
        if b.net is not None:
            driver = _render_lvnet_source(b.net, handles)
            default = None
        else:
            assert b.default is not None
            driver = None
            default = _strip_default_prefix(_lvnet_default_trailing(b.default))
        entries.append(("in", b.terminal, type_shape, driver, default, b.inverted))
    for o in sorted(instance.outputs, key=lambda o: o.pane_rank):
        if _is_void_type(o.type):
            continue
        type_shape = (
            _lv_type_comparison_shape(o.lv_type, ambiguous=ambiguous)
            if o.lv_type is not None
            else ("leaf", o.type)
        )
        entries.append(("out", o.net.terminal, type_shape, None, None, False))
    return tuple(entries)


def _module_instance_signature(
    instance: NetlistInstance, handles: _LvnetHandles, ambiguous: frozenset[str]
) -> tuple:
    if instance.kind == NetlistInstanceKind.LOCAL_VARIABLE:
        # Matches ``_parsed_item_signature``'s ``ParsedLocalVariable`` branch
        # exactly -- see ``_render_lvnet_local_variable`` for the shape this
        # mirrors (a write's single input's ``net``/``default``, a read's
        # empty ``inputs``).
        handle = handles.by_uid[instance.uid]
        if instance.inputs:
            binding = instance.inputs[0]
            if binding.net is not None:
                source_str = _render_lvnet_source(binding.net, handles)
            else:
                assert binding.default is not None
                source_str = _lvnet_default_token(binding.default)
            return ("local-variable", handle, "write", source_str)
        return ("local-variable", handle, "read")
    header_kw = _LVNET_INSTANCE_KEYWORDS[instance.kind]
    handle = handles.by_uid[instance.uid]
    component = _lvnet_component(instance)
    terminals = _module_terminal_tuple(instance, handles, ambiguous)
    has_todo = instance.kind in _OPEN_INSTANCE_TRAILING_TODO
    return ("node", header_kw, handle, component, terminals, has_todo)


def _module_constant_signature(const: NetlistConstant, handles: _LvnetHandles) -> tuple:
    handle = handles.by_uid[const.uid]
    # ``lvnet_value`` (lvnet-escaped, md §4/§10) -- what ``_render_lvnet_
    # constant`` actually emits -- NOT ``const.value`` (the OLD render_
    # netlist/netlist_to_dict-parity text, unescaped).
    return ("constant", handle, const.type, const.lvnet_value)


def _module_feedback_signature(fb: NetlistFeedback, handles: _LvnetHandles) -> tuple:
    if fb.delay is None:
        attr = "? iterations"
    else:
        attr = f"{fb.delay} iteration" + ("" if fb.delay == 1 else "s")
    init_str = _render_lvnet_source(fb.init, handles)
    each_str = _render_lvnet_source(fb.recur, handles) if fb.recur is not None else None
    return ("feedback", fb.net, attr, init_str, each_str)


def _module_case_scope_signature(
    scope: NetlistScope, handles: _LvnetHandles, ambiguous: frozenset[str]
) -> tuple:
    sel = (
        _render_lvnet_source(scope.selector, handles)
        if scope.selector is not None
        else "?"
    )
    gammas = [m for m in scope.outputs if isinstance(m, GammaMerge)]
    frames: list[tuple] = []
    for frame in scope.frames:
        label = _quoted_frame_label(frame.label)
        body_sig = _module_body_signature(frame.body, handles, ambiguous)
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


def _module_loop_scope_signature(
    scope: NetlistScope, handles: _LvnetHandles, ambiguous: frozenset[str]
) -> tuple:
    kind_word = "while-loop" if scope.kind == "while" else "for-loop"
    body_sig = _module_body_signature(scope.frames[0].body, handles, ambiguous)
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


def _module_frame_only_scope_signature(
    kind_word: str,
    frame_labels_and_bodies: list[tuple[str, list[NetlistItem]]],
    handles: _LvnetHandles,
    ambiguous: frozenset[str],
) -> tuple:
    """Shared by the three frame-only scope families (sequence/disabled/
    event, see ``_parse_labeled_frames``'s docstring for why they share one
    shape): none of them carry an output MERGE (``NetlistScope.outputs`` is
    always empty for these kinds), so a frame's signature is just its own
    already-rendered label + its body -- no drive-entries computation like
    ``_module_case_scope_signature``'s (kept as an empty tuple for
    STRUCTURAL symmetry with a case frame's 3-tuple shape, so
    ``_parsed_item_signature``'s matching branch doesn't need a special
    case)."""
    frames = tuple(
        (label, _module_body_signature(body, handles, ambiguous), ())
        for label, body in frame_labels_and_bodies
    )
    return ("scope", kind_word, None, frames)


def _module_sequence_scope_signature(
    scope: NetlistScope, handles: _LvnetHandles, ambiguous: frozenset[str]
) -> tuple:
    """``flat-sequence``/``stacked-sequence`` (§8) -- ``kind_word`` picked
    from ``scope.sequence_is_flat``, the label composed EXACTLY as
    ``_render_lvnet_sequence_scope`` does (``[<value>]``, never quoted)."""
    kind_word = "flat-sequence" if scope.sequence_is_flat else "stacked-sequence"
    return _module_frame_only_scope_signature(
        kind_word,
        [(f"[{frame.value}]", frame.body) for frame in scope.frames],
        handles,
        ambiguous,
    )


def _module_disabled_scope_signature(
    scope: NetlistScope, handles: _LvnetHandles, ambiguous: frozenset[str]
) -> tuple:
    """``diagram-disable``/``conditional-disable``/``type-specialization``
    (§8) -- ``kind_word`` from ``scope.disable_kind`` via the SAME
    ``_LVNET_DISABLE_KEYWORD`` table the renderer uses; a Conditional
    Disable frame's label is quoted (its decoded symbol condition), the
    other two kinds' labels (``Enabled``/``Disabled``/``[i]``) are bare --
    matching ``_render_lvnet_disabled_scope`` exactly."""
    kind_word = _LVNET_DISABLE_KEYWORD[scope.disable_kind]
    quote = scope.disable_kind is DisableStructureKind.CONDITIONAL
    return _module_frame_only_scope_signature(
        kind_word,
        [
            (
                _quoted_frame_label(frame.label) if quote else frame.label,
                frame.body,
            )
            for frame in scope.frames
        ],
        handles,
        ambiguous,
    )


def _module_event_scope_signature(
    scope: NetlistScope, handles: _LvnetHandles, ambiguous: frozenset[str]
) -> tuple:
    """``event-structure`` (§8) -- every frame label quoted, matching
    ``_render_lvnet_event_scope`` exactly."""
    return _module_frame_only_scope_signature(
        "event-structure",
        [(_quoted_frame_label(frame.label), frame.body) for frame in scope.frames],
        handles,
        ambiguous,
    )


def _module_body_signature(
    items: list[NetlistItem], handles: _LvnetHandles, ambiguous: frozenset[str]
) -> tuple:
    out: list[tuple] = []
    for item in items:
        if isinstance(item, NetlistInstance):
            out.append(_module_instance_signature(item, handles, ambiguous))
        elif isinstance(item, NetlistScope):
            if item.kind == "case":
                out.append(_module_case_scope_signature(item, handles, ambiguous))
            elif item.kind in ("for", "while"):
                out.append(_module_loop_scope_signature(item, handles, ambiguous))
            elif item.kind == "sequence":
                out.append(_module_sequence_scope_signature(item, handles, ambiguous))
            elif item.kind == "disabled":
                out.append(_module_disabled_scope_signature(item, handles, ambiguous))
            elif item.kind == "event":
                out.append(_module_event_scope_signature(item, handles, ambiguous))
            else:
                raise LvnetUnsupportedConstructError(
                    f"netlist_signature does not yet cover scope kind "
                    f"{item.kind!r}"
                )
        elif isinstance(item, NetlistFeedback):
            out.append(_module_feedback_signature(item, handles))
        elif isinstance(item, NetlistConstant):
            out.append(_module_constant_signature(item, handles))
    return tuple(out)


_FRAME_ONLY_SCOPE_KINDS = frozenset(
    {
        "flat-sequence",
        "stacked-sequence",
        "diagram-disable",
        "conditional-disable",
        "type-specialization",
        "event-structure",
    }
)


def _parsed_item_signature(
    item: ParsedBodyItem,
    types_dict: dict[str, ParsedTypeDef],
    ambiguous: frozenset[str],
) -> tuple:
    """``types_dict`` (``ParsedLvnet.types``, the §10 footnote defs) and
    ``ambiguous`` (``netlist._lvnet_ambiguous_named_types``) are threaded
    through every recursive call so a node's own terminal types
    (``ParsedNode.terminals``) can resolve a by-name reference to its full
    structure -- see ``_parsed_type_ref_shape``. Constants/feedback/tunnel
    sources carry no structured type of their own (unchanged, plain-text
    comparison, matching ``netlist.NetlistConstant``'s own scope -- see
    ``_iter_lv_types_in_items``'s docstring)."""
    if isinstance(item, ParsedConstant):
        return ("constant", item.handle, item.type, item.value)
    if isinstance(item, ParsedLocalVariable):
        # Matches ``_module_instance_signature``'s ``NetlistInstanceKind.
        # LOCAL_VARIABLE`` branch exactly.
        if item.is_write:
            return ("local-variable", item.handle, "write", item.source)
        return ("local-variable", item.handle, "read")
    if isinstance(item, ParsedFeedback):
        return ("feedback", item.net, item.attribute, item.init, item.each)
    if isinstance(item, ParsedScope):
        if item.kind == "case" or item.kind in _FRAME_ONLY_SCOPE_KINDS:
            # Same 3-tuple frame shape as a case scope (label, body, drives)
            # -- §8's sequence/disabled/event families just never populate
            # ``drives`` (see ``_parse_labeled_frames``: none of them carry
            # an output merge to drive, so ``f.drives`` is always ``()``).
            #
            # A CASE frame's label is re-normalized through
            # ``_quoted_frame_label`` here -- matching
            # ``_module_case_scope_signature``'s own call on the module side
            # -- because a pure-default frame's ``ParsedFrame.label`` is the
            # BARE ``"Default"`` sentinel (§8's ``frame default :`` keyword
            # carries no quotes of its own to keep verbatim, unlike every
            # other case-frame label), so without this it would compare
            # unequal to the module side's quoted ``'"Default"'``. Every
            # other case-frame label is already quoted verbatim from the
            # text, so this is a no-op passthrough for them. The frame-only
            # families are UNCHANGED (bare stays bare, matching their own
            # module-side signature builders exactly).
            frames = tuple(
                (
                    _quoted_frame_label(f.label) if item.kind == "case" else f.label,
                    tuple(
                        _parsed_item_signature(i, types_dict, ambiguous)
                        for i in f.body
                    ),
                    tuple((d.net, d.source) for d in f.drives),
                )
                for f in item.frames
            )
            return ("scope", item.kind, item.selector, frames)
        body_sig = tuple(
            _parsed_item_signature(i, types_dict, ambiguous) for i in item.body
        )
        shift_regs = tuple((sr.net, sr.init, sr.each) for sr in item.shift_registers)
        tunnels = tuple((t.net, t.mode, t.source) for t in item.tunnels)
        return ("scope", item.kind, None, body_sig, shift_regs, tunnels)
    if isinstance(item, ParsedNode):
        terminals = tuple(
            (
                t.direction,
                t.name,
                _parsed_type_ref_shape(t.type, types_dict, ambiguous=ambiguous),
                t.driver,
                t.default,
                t.inverted,
            )
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


def netlist_signature(
    module_or_parsed: NetlistModule | ParsedLvnet,
    ambiguous_named_types: frozenset[str] | None = None,
) -> tuple:
    """The full, comparable projection of an lvnet document: ``(uses,
    pattern_id, boundary, body, drives)`` -- the ``uses :`` manifest, the
    Phase 2 ``front-panel :`` section's own ``pattern_id``
    (``ConnectorPane.pattern_id``) plus its boundary block, the
    ``block-diagram :`` body, and the final boundary-output-drive block --
    computed IDENTICALLY from a real ``NetlistModule`` (reusing
    ``render_lvnet``'s own private helpers directly, e.g.
    ``_lvnet_component``/``_render_lvnet_source``/``netlist._lv_type_
    comparison_shape``, so the module side is provably what the text would
    say) and from a ``ParsedLvnet``. Excludes uid and any derived/non-
    textual field, same principle as ``boundary_signature``.

    Every type reference anywhere in the comparison (boundary, ``uses :``
    dependency interfaces, node terminals) is now the STRENGTHENED §10
    shape: a plain ``("leaf", label)`` for anything with no ``types :``
    footnote to rehydrate from (compares by its existing inline structural
    form, unchanged), or the type's FULL resolved structure once a NAMED
    enum/ring/cluster is reached -- resolved from the real ``LVType``
    graph on the module side (``_lv_type_comparison_shape``) and through
    the parsed ``types :`` footnote dict on the parsed side
    (``_parsed_type_ref_shape``, fed ``parsed.types``) -- so a passing
    round-trip now proves TYPE REHYDRATION, not just by-name equality.

    ``ambiguous_named_types`` (``netlist._lvnet_ambiguous_named_types``) is
    the set of names EXCLUDED from that strengthened resolution -- a name
    that genuinely resolves to more than one distinct structure at
    different occurrences in the module (a Variant-typed field, observed
    on a real corpus VI), where the flat one-entry-per-name ``types :``
    footnote can't be trusted for every occurrence. Module side: ``None``
    (the default) computes it fresh from ``module`` itself. Parsed side:
    ``None`` defaults to the empty set (a bare ``ParsedLvnet`` has no
    independent way to detect this from text alone) -- a caller comparing
    a parsed reproduction of a SPECIFIC module's own render should compute
    ``netlist.``:func:`_lvnet_ambiguous_named_types` from that module ONCE
    and pass the SAME set to both calls, so the two sides apply an
    identical exclusion.

    Every §8 structure kind is now covered (case/for-loop/while-loop/
    flat-sequence/stacked-sequence/diagram-disable/conditional-disable/
    type-specialization/event-structure); ``LvnetUnsupportedConstructError``
    (module side) / ``LvnetParseError`` (parsed side) remain as the loud
    failure for a genuinely new construct outside that set, rather than
    silently producing an incomparable signature.
    """
    if isinstance(module_or_parsed, ParsedLvnet):
        parsed = module_or_parsed
        types_dict = parsed.types
        ambiguous = (
            ambiguous_named_types if ambiguous_named_types is not None else frozenset()
        )
        uses = tuple(
            (
                d.kind,
                d.qualified,
                d.path,
                tuple(
                    (
                        t.direction,
                        t.name,
                        _parsed_type_ref_shape(t.type, types_dict, ambiguous=ambiguous),
                    )
                    for t in d.interface
                ),
            )
            for d in parsed.uses
        )
        boundary = boundary_signature(parsed, types_dict, ambiguous)
        body = tuple(
            _parsed_item_signature(item, types_dict, ambiguous) for item in parsed.body
        )
        drives = tuple((d.net, d.source) for d in parsed.output_drives)
        return (uses, parsed.pattern_id, boundary, body, drives)

    module = module_or_parsed
    ambiguous = (
        ambiguous_named_types
        if ambiguous_named_types is not None
        else _lvnet_ambiguous_named_types(module)
    )
    uses = tuple(
        (
            d.kind.value,
            d.qualified,
            d.path,
            _dependency_interface_signature(d.interface, ambiguous),
        )
        for d in module.dependencies
    )
    handles = _assign_lvnet_handles(module)
    boundary = boundary_signature(module, ambiguous=ambiguous)
    body = _module_body_signature(module.body, handles, ambiguous)
    drives = tuple(
        (
            o.name,
            _render_lvnet_source(o.source, handles) if o.source is not None else "?",
        )
        for o in module.outputs
    )
    return (uses, module.connector_pane.pattern_id, boundary, body, drives)
