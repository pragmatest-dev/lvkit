"""lvnet grammar constants -- the exact punctuation/keyword literals the
lvnet text format (§2-§10) is built from.

Defined ONCE here (this module is the grammar's render source-of-truth) and
imported verbatim into ``netlist.py`` (render), ``lvnet_parse.py`` (parse),
and ``lvnet_reconstruct.py`` (rehydrate), so render/parse/reconstruct can
never silently drift onto two slightly different literals for the same
token. This is a LEAF module -- it imports nothing from ``.netlist``,
``.lvnet_parse``, or ``.lvnet_reconstruct``.
"""

from __future__ import annotations

import re

from ..models import DisableStructureKind
from .netlist_models import NetlistInstanceKind

# ------------------------------------------------------------------
# Grammar delimiters -- the exact punctuation/keyword literals §2-§10 is
# built from. Defined ONCE here (this module is the grammar's render
# source-of-truth) and imported verbatim into lvnet_parse.py/lvnet_
# reconstruct.py, so a round-trip can never silently break from render and
# parse drifting onto two slightly different literals for the same token.
#
# Two near-identical-looking fragments are DELIBERATELY kept separate,
# never folded together:
# - ``_LVNET_TYPE_SEP`` (`` : ``, fixed-width) vs. ``_LVNET_BLOCK_OPEN``
#   (`` :``, no trailing space) -- a terminal's ``name : Type`` clause is a
#   different grammar role from a construct header's trailing block-opener
#   (``case <sel> :``, ``vi <name> :``, ``while-loop :``, ...), even though
#   both happen to contain a colon.
# - A COLUMN-ALIGNED line (``_render_term_group``'s ``: ``/its caller's
#   ``= `` trailing text, and the padded output-drive line's own ``= ``)
#   never routes through these fixed-width constants -- the leading space
#   there comes from ``str.ljust`` padding, not from the literal itself, so
#   gluing those call sites to the 3-char constants would either double a
#   space or force a derivation for no real drift-safety gain (the parse
#   side recovers the split by SEARCHING for the fixed-width token with
#   ``str.find``, which already tolerates arbitrary extra padding either
#   side of it). Left as documented one-off literals at their call sites.
# ------------------------------------------------------------------
# The single indent unit -- ONE nesting level of every lvnet block. The whole
# renderer nests by string-concatenating this (``indent + _LVNET_INDENT``); the
# parser recovers a level by its width (``_LVNET_INDENT_WIDTH``), so the two
# sides share one source of truth and can't drift on how deep a level is.
_LVNET_INDENT = "  "
_LVNET_INDENT_WIDTH = len(_LVNET_INDENT)
_LVNET_TYPE_SEP = " : "  # `name : Type` / `handle : component` (§3/§7)
_LVNET_DRIVER_OP = " = "  # `= driver` / `name = def` (§4/§7/§8/§10)
_LVNET_BLOCK_OPEN = " :"  # trailing block-opener (§2/§7/§8)
_LVNET_ANNOTATION_SEP = " ; "  # fixed-width trailing-annotation sep (§6)
_LVNET_TERMINAL_SEP = "::"  # `<handle>::<terminal>` / structure-scoped net (§9)
_LVNET_TYPEDEF_NAV_PREFIX = "./"  # the `; ./path` nav clause's own prefix
# The `uses :` manifest's own qualified;path separator -- a padding-
# tolerant sibling of ``_LVNET_ANNOTATION_SEP`` (2 chars, not 3: the space
# before it comes from ``_lvnet_capped_pad``'s column padding, same
# reasoning as the column-aligned one-offs above) -- kept as its own named
# constant rather than a one-off because, unlike those, BOTH render
# (``_render_lvnet_uses``) and parse (``_parse_uses_block``) spell it out
# as a literal, so it is a genuine cross-file drift risk.
_LVNET_DEP_PATH_SEP = "; "
_LVNET_ENUM_OPEN = "Enum{"  # §10 lossless enum/ring/cluster open tokens
_LVNET_RING_OPEN = "Ring{"
_LVNET_CLUSTER_OPEN = "Cluster{"
_LVNET_DEFAULT_KEYWORD = "default"  # the §4 unwired-default keyword
# The drive-position `(default <Type>)` form's own prefix (§4) -- derived
# from ``_LVNET_DEFAULT_KEYWORD`` rather than re-spelled, so the two can
# never drift apart.
_LVNET_DEFAULT_PAREN_PREFIX = f"({_LVNET_DEFAULT_KEYWORD} "
# The OPTIONAL bottom-appendix `types :` footnote section header (§10) and
# the OPTIONAL `uses :` dependency-manifest header (§2/§7) -- each its own
# full line (2-space indent), matched verbatim by lvnet_parse.py.
_TYPES_HEADER_LINE = f"{_LVNET_INDENT}types :"
_USES_HEADER_LINE = f"{_LVNET_INDENT}uses :"
# Phase 2 (lvnet redesign): the LV-mirroring section layout -- a VI's own
# connector pane (`front-panel :`, OPTIONAL -- omitted when the pane is
# empty and the pattern is unknown) and its diagram body (`block-diagram :`,
# ALWAYS present). Both are 2-space-indent section headers, exactly like
# `uses :`/`types :` above -- their own content nests one level deeper (4
# spaces), matched verbatim by lvnet_parse.py.
_FRONT_PANEL_HEADER_LINE = f"{_LVNET_INDENT}front-panel :"
_BLOCK_DIAGRAM_HEADER_LINE = f"{_LVNET_INDENT}block-diagram :"
# The `front-panel :` section's own `pattern : <conId>` line keyword (§2's
# connector-pane identity) and the unconditional `@<index>` trailing column
# every ON-PANE boundary terminal row gains (present in BOTH terse and
# verbose -- pane slot identity is structural, not a lossless-verbosity
# nicety, unlike the `<requirement>`/`default <value>` clause it sits next
# to). OFF-PANE terminals (a front-panel control not on the connector pane)
# are not surfaced by this pass -- deferred, needs build-side plumbing.
_LVNET_PATTERN_KEYWORD = "pattern"
_LVNET_PANE_INDEX_PREFIX = "@"

# Column-alignment caps (lvnet §14: "density is a view concern"). A single
# outlier terminal -- a named enum with ~300 members shown structurally
# because it happens to be anonymous, or just a long field/type name --
# must not drag every SIBLING line's column out to match it (the event-VI
# regression this pass fixes: one 1267-char line of near-total whitespace).
# Chosen so a normal name/type ("methodName (\"runTest\")", "TestCase.lvclass")
# aligns exactly as before; only a genuine outlier overflows on its own.
_LVNET_NAME_CAP = 32
_LVNET_TYPE_CAP = 40

# A structure-scoped net name (``case_UID.outK``/``loop_UID.shiftK``/
# ``loop_UID.outK`` -- built by ``_gamma_net_name_gn``/``_eta_net_name_gn``/
# ``_mu_net_name_gn``; Phase 3: ``UID`` is the structure's own stable BD uid,
# not a small per-structure counter) always has this exact
# ``<prefix>_<uid>.<rest>`` shape -- ONE dot, never more. A boundary
# control's bare name and a feedback net (``fbK``) have no dot at all and
# never match.
_LVNET_STRUCTURE_NET_RE = re.compile(r"^((?:case|loop)_\d+)\.(.+)$")

# ``LOCAL_VARIABLE`` (now designed, §7 revised again): a read/write TAP on a
# control's own net, not a generic `<keyword> <handle> : <component>` +
# terminal-block declaration -- it carries no component identity of its own
# (the tapped control's own `front-panel :` row already names the type) and
# no terminal block (a read's single output/a write's single input is spelled
# inline: `local-variable <handle> : read` / `local-variable <handle> :
# write = <source>`). Rendered by its own dedicated branch in
# ``_render_lvnet_instance`` -- kept OUT of ``_LVNET_INSTANCE_KEYWORDS`` below
# for the same reason ``feedback-node`` is: its shape doesn't fit the generic
# dict-lookup + terminal-block path.

# The §7 header keyword for every instance kind that DOES declare itself via
# the generic ``<keyword> <handle> : <component>`` + terminal-block form
# (everything except ``LOCAL_VARIABLE``, handled separately above, and
# ``NetlistFeedback`` -- not a ``NetlistInstanceKind`` at all).
_LVNET_INSTANCE_KEYWORDS: dict[NetlistInstanceKind, str] = {
    NetlistInstanceKind.SUBVI: "subVI",
    NetlistInstanceKind.FUNCTION: "function",
    NetlistInstanceKind.PROPERTY_NODE: "property-node",
    NetlistInstanceKind.INVOKE_NODE: "invoke-node",
    NetlistInstanceKind.IN_PLACE_ELEMENT: "in-place-element",
    NetlistInstanceKind.FORMULA_NODE: "formula-node",
}

# A trailing ``# TODO(lvnet): ...`` for the ONE part of an otherwise-fully-
# rendered declaration that §17 item 6 still leaves undesigned. Absent here
# (SUBVI/FUNCTION/PROPERTY_NODE/INVOKE_NODE) means nothing is undesigned --
# the declaration + terminal block is the WHOLE rendering, per §7's table.
_OPEN_INSTANCE_TRAILING_TODO: dict[NetlistInstanceKind, str] = {
    NetlistInstanceKind.IN_PLACE_ELEMENT: (
        "in-place-element decompose/recompose pairing was never designed "
        "(md §17 item 6)"
    ),
    NetlistInstanceKind.FORMULA_NODE: (
        "formula-node script rendering needs the `script` field plumbed "
        "onto the model first (md §17 item 6)"
    ),
}

# ``EtaMerge.index_mode``'s internal short code -> lvnet §8's border-construct
# WORD ("mode: auto-indexing | last-value | concatenating | pass-through").
# ``index_mode`` is already computed by ``_eta_index_mode``; this is purely a
# display remap, not a new semantic derivation.
_LVNET_TUNNEL_MODE_WORD: dict[str, str] = {
    "array": "auto-indexing",
    "last": "last-value",
    "concat": "concatenating",
    "passthrough": "pass-through",
}

# ``DisableStructureKind`` -> lvnet §8's disable-family structure KEYWORD.
# Straight from the §8 table -- no invented word.
_LVNET_DISABLE_KEYWORD: dict[DisableStructureKind, str] = {
    DisableStructureKind.DIAGRAM: "diagram-disable",
    DisableStructureKind.CONDITIONAL: "conditional-disable",
    DisableStructureKind.TYPE_SPEC: "type-specialization",
}

# The lvnet §4/§10 string-literal escape table: standard backslash escapes
# for the four control chars a real LabVIEW string constant is actually
# observed to carry (CR, LF, TAB, plus the two syntactic chars the quoting
# itself introduces -- a literal backslash and a literal double-quote).
# Anything else in the C0 control range (U+0000-U+001F) -- unobserved in the
# corpus but not excludable -- falls through to a `\xHH` escape below rather
# than being guessed at. This is the ONE lvnet literal-value escape table;
# ``lvnet_parse._LVNET_STRING_UNESCAPES`` is its exact reverse.
_LVNET_STRING_ESCAPES: dict[str, str] = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}

# §14-style column-alignment caps for the ``uses :`` manifest (mirrors
# ``_LVNET_NAME_CAP``/``_LVNET_TYPE_CAP`` above) -- a long qualified identity
# (``Class.lvclass:VeryLongSubVIName.vi``) overflows on its own line instead
# of stretching every sibling dependency line's column out to match it.
_LVNET_DEP_KIND_CAP = 12
_LVNET_DEP_QUALIFIED_CAP = 60

# Indent of a ``uses :`` entry's inline §7a interface lines -- one level
# (2 spaces) deeper than the entry's own 4-space indent, matching the SAME
# "header, then body at +2" rule every other lvnet block follows (a node's
# own in/out block nests at ``indent + _LVNET_INDENT`` under its declaration --
# see ``_render_lvnet_instance``).
_LVNET_DEP_INTERFACE_INDENT = _LVNET_INDENT * 3
