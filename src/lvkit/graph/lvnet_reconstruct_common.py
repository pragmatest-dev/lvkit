"""Helpers every part of the lvnet reconstruction shares: the error it raises,
what a declared handle resolves to, and the reverse of how a literal or a net
reference is written."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass

from ..models import ScalarValue
from .lvnet_grammar import (
    _LVNET_DEFAULT_PAREN_PREFIX,
    _LVNET_NUMBER_ITEM_RE,
    _LVNET_STRUCTURE_NET_TEXT_RE,
    _LVNET_TERMINAL_SEP,
    split_id_suffix,
)
from .lvnet_parse import (
    ParsedBodyItem,
    ParsedFeedback,
    ParsedGlobalVariable,
    ParsedIndicatorDrive,
    ParsedLocalVariable,
    ParsedLvnet,
    ParsedNode,
    ParsedScope,
    _scan_quoted_literal,
    _unescape_lvnet_string,
    _unquote_name_token,
)
from .netlist_models import DefaultValue, NetRef


class LvnetReconstructError(ValueError):
    """A parsed lvnet document could not be reconstructed into a renderable
    ``NetlistModule`` -- a genuine shape this module doesn't (yet) know how
    to reverse, named explicitly rather than guessed. Kept a separate
    exception from ``LvnetParseError`` (the TEXT-level grammar failure):
    this one fires only on a document that parsed fine but whose semantic
    shape this reconstructor can't rebuild a model for.
    """


@dataclass(frozen=True)
class HandleTarget:
    """What a declared handle resolves to, for every later ``<handle>::
    <terminal>`` (or bare, for a labeled constant) reference to it: ``name``
    (so ``_assign_lvnet_handles`` re-derives the SAME BASE on the way back
    out) and ``uid`` -- the node's own real BD uid, recovered from
    the handle's own ``_<uid>`` suffix (``_handle_base_and_suffix``), not
    minted. ``uid`` serves both as the reconstructed instance/constant's own
    ``NetlistInstance.uid``/``NetlistConstant.uid`` and, for a later node-
    terminal reference, as ``NetRef.producer_uid`` -- the SAME identity
    ``_assign_lvnet_handles``/``_render_lvnet_source`` resolve through
    ``handles.by_uid`` on the way back out."""

    name: str
    uid: str
    is_constant: bool
    # A front-panel control's quoted name (see ``reconstruct_module``).
    is_boundary: bool = False


def parse_lvnet_literal(token: str) -> ScalarValue:
    """The reverse of ``render_lvnet.lvnet_literal_token``: a rendered literal
    VALUE token (a boundary control's own ``default <value>`` text) back to
    a raw ``ScalarValue``. Raises on anything that isn't one of the four
    shapes ``lvnet_literal_token`` ever emits (quoted string / ``True`` /
    ``False`` / a bare number) -- never guesses."""
    token = token.strip()
    number_item = _LVNET_NUMBER_ITEM_RE.match(token)
    if number_item:
        token = number_item.group(1)
    if token.startswith('"'):
        end = _scan_quoted_literal(token, 0)
        if end != len(token):
            raise LvnetReconstructError(
                f"trailing text after quoted literal: {token!r}"
            )
        return _unescape_lvnet_string(token)
    if token == "True":
        return True
    if token == "False":
        return False
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    raise LvnetReconstructError(f"cannot parse lvnet literal token: {token!r}")


def is_numeric_literal(text: str) -> bool:
    """A number, alone or followed by the enum or ring item it names (`2 (verify)`)."""
    if _LVNET_NUMBER_ITEM_RE.match(text):
        return True
    try:
        float(text)
        return True
    except ValueError:
        return False


def _control_ref(control: HandleTarget, uid: str | None) -> NetRef:
    return NetRef(
        node=None,
        terminal=control.name,
        occurrence=None,
        bare=control.name,
        boundary=True,
        boundary_uid=uid,
    )


def parse_source_token(
    text: str, registry: dict[str, HandleTarget]
) -> NetRef | DefaultValue:
    """The reverse of ``netlist._render_lvnet_source``: a rendered
    ``= <driver>``/merge-source VALUE token back to a ``NetRef`` (a net
    reference: node-terminal, boundary control, structural net, or an inlined
    literal) or a ``DefaultValue`` (the drive-position ``(default <Type>)``
    form -- an unwired case-output tunnel/shift-register/feedback init).

    A plain literal token (quoted string / ``True``/``False``/number) is
    reconstructed as a ``NetRef`` with ``node=None`` and ``lvnet_value`` set
    to the SAME text. A drive-position ``(default <Type>)`` is always an
    unwired default, never a wired constant.
    """
    # Only LEADING whitespace is stripped; a TRAILING space is preserved
    # because it can be part of a real net/terminal NAME (a LabVIEW control
    # literally named ``"Analog Acquisition "``) -- ``_render_lvnet_source``
    # re-emits the stored ``terminal`` verbatim, so stripping it here would
    # lose it on round-trip. The value is extracted raw (no alignment padding
    # trails a driver), so there is no spurious trailing whitespace to shed.
    text = text.lstrip()
    if text.startswith(_LVNET_DEFAULT_PAREN_PREFIX) and text.endswith(")"):
        return DefaultValue(
            literal="?", type_descriptor=text[len(_LVNET_DEFAULT_PAREN_PREFIX) : -1]
        )
    named, uid = split_id_suffix(text)
    if uid is not None:
        # A control named by its terminal (its label is shared).
        control = registry.get(text)
        if control is None or not control.is_boundary:
            raise LvnetReconstructError(f"{named!r} names no control (id {uid})")
        return _control_ref(control, uid)
    if text.startswith('"'):
        end = _scan_quoted_literal(text, 0)
        if end != len(text):
            raise LvnetReconstructError(
                f"trailing text after quoted literal in source: {text!r}"
            )
        control = registry.get(text)
        if control is not None and control.is_boundary:
            return _control_ref(control, None)
        return NetRef(
            node=None, terminal="", occurrence=None, bare=text, lvnet_value=text
        )
    if text in ("True", "False") or is_numeric_literal(text):
        return NetRef(
            node=None, terminal="", occurrence=None, bare=text, lvnet_value=text
        )
    if _LVNET_TERMINAL_SEP in text:
        handle_part, _, terminal = text.partition(_LVNET_TERMINAL_SEP)
        if terminal.startswith('"'):
            terminal = _unquote_name_token(terminal)
        target = registry.get(handle_part)
        if target is not None and not target.is_constant:
            # ``producer_uid`` is the recovered identity:
            # ``_render_lvnet_source`` resolves it straight through
            # ``handles.by_uid`` on the way back out (as it does a labeled
            # constant's ``constant_uid``); ``occurrence`` takes no part in it.
            return NetRef(
                node=target.name,
                terminal=terminal,
                occurrence=None,
                bare=text,
                producer_uid=target.uid,
            )
        if _LVNET_STRUCTURE_NET_TEXT_RE.match(text):
            # A structure-scoped net (`case_UID::outK`, `loop_UID::shiftK`, ...).
            return NetRef(node=None, terminal="", occurrence=None, bare=text)
        # A producer the diagram does not name (a wire from a node that is not
        # there): the model keeps it as `<node>.<terminal>`.
        return NetRef(
            node=handle_part,
            terminal=terminal,
            occurrence=None,
            bare=f"{handle_part}.{terminal}",
        )
    target = registry.get(text)
    if target is not None and target.is_constant:
        return NetRef(
            node=None, terminal="", occurrence=None, bare=text, constant_uid=target.uid
        )
    if target is not None and target.is_boundary:
        return _control_ref(target, None)
    # A bare structural net (`fbK`) renders via the "node is None" fallback.
    return NetRef(node=None, terminal="", occurrence=None, bare=text)


def require_netref(source: NetRef | DefaultValue, where: str) -> NetRef:
    if not isinstance(source, NetRef):
        raise LvnetReconstructError(
            f"expected a net reference at {where}, got a bare default: {source!r}"
        )
    return source


# ============================================================
# Types (the reverse of lvnet_type_lossless_def / lvnet_type_ref)
# ============================================================


def _item_sources(item: ParsedBodyItem) -> Iterator[str]:
    """Every source text a parsed body item writes, nested bodies included."""
    if isinstance(item, ParsedNode):
        yield from (t.driver for t in item.terminals if t.driver is not None)
    elif isinstance(item, ParsedLocalVariable | ParsedGlobalVariable):
        if item.source is not None:
            yield item.source
    elif isinstance(item, ParsedFeedback):
        yield from (x for x in (item.init, item.each) if x is not None)
    elif isinstance(item, ParsedIndicatorDrive):
        yield item.source
    elif isinstance(item, ParsedScope):
        if item.selector is not None:
            yield item.selector
        for sr in item.shift_registers:
            yield from (x for x in (sr.init, sr.each) if x is not None)
        for tunnel in item.tunnels:
            yield tunnel.source
            if tunnel.condition is not None:
                yield tunnel.condition
        yield from (d.source for d in (*item.drives, *item.inputs))
        for frame in item.frames:
            yield from (d.source for d in frame.drives)
            for inner in frame.body:
                yield from _item_sources(inner)
        for inner in item.body:
            yield from _item_sources(inner)


def referenced_terminals(
    parsed: ParsedLvnet, registry: dict[str, HandleTarget]
) -> set[tuple[str, str]]:
    """The ``(producer uid, terminal)`` of every node output the document names
    as a source: the outputs a wire leaves."""
    sources = [s for item in parsed.body for s in _item_sources(item)]
    sources.extend(d.source for d in parsed.output_drives)
    found: set[tuple[str, str]] = set()
    for text in sources:
        ref = parse_source_token(text, registry)
        if isinstance(ref, NetRef) and ref.producer_uid is not None:
            found.add((ref.producer_uid, ref.terminal))
    return found


def check_shared_references(
    parsed: ParsedLvnet, registry: dict[str, HandleTarget]
) -> None:
    """A label that several controls share is named by terminal wherever a wire
    reads it; terse text names none, so it cannot be read back."""
    counts = Counter(r.name for r in parsed.boundary if r.direction == "in")
    shared = {n for n, k in counts.items() if k > 1}
    if not shared:
        return
    sources = [s for item in parsed.body for s in _item_sources(item)]
    sources.extend(d.source for d in parsed.output_drives)
    for text in sources:
        ref = parse_source_token(text, registry)
        if (
            isinstance(ref, NetRef)
            and ref.boundary
            and ref.boundary_uid is None
            and ref.terminal in shared
        ):
            raise LvnetReconstructError(
                f"{ref.terminal!r} labels several controls and a wire reads it: "
                f"terse text cannot say which; use verbose text, which gives "
                f"each its terminal id"
            )
