"""Where a diff change sits among frames, and which frames of two versions are
the same frame.

Within one version a frame is addressed by its structure's uid and its position
in that structure's frames (``FrameStep``; the rendered SVG's ``data-path``
uses the same address). Across the two versions a structure's frames are
matched by value, then by contents (``match_frames``); every matched structure
records its pairs (``FramePairs``), and ``FrameMatching`` maps a before-version
location onto the after version through them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from ..models import CaseFrame, EventFrame, Frame, SequenceFrame
from .core import uid_of
from .models import (
    AnyGraphNode,
    CaseStructureNode,
    DisableStructureNode,
    EventStructureNode,
    SequenceNode,
)
from .netlist_build import case_frame_label_gn, case_selector_type_gn
from .op_walk import _selector_label, selector_shown_values


def uid_sort(uid: str) -> tuple[int, object]:
    """Numeric uids in numeric order, then the rest."""
    return (0, int(uid)) if uid.isdigit() else (1, uid)


def frames_of(op: AnyGraphNode) -> Sequence[Frame] | None:
    """The frame list of a FRAME-BEARING structure, else None — the single
    'is this a frame-set structure?' test. Every kind here has its changes
    represented as a frame-set diff (added / removed / value-changed); Disable's
    frames are CaseFrames and Event's are EventFrames. A LoopNode is a structure
    but NOT here: it has one unconditional body, not a selectable frame set. (The
    literal isinstance lives ONLY here so the type checker can narrow ``op`` to a
    ``.frames``-bearing type — pyright won't narrow on a tuple stored in a
    variable.)"""
    if isinstance(
        op,
        (CaseStructureNode, SequenceNode, EventStructureNode, DisableStructureNode),
    ):
        return op.frames
    return None


def is_interactive_struct(op: AnyGraphNode) -> bool:
    """Whether ``op`` is wrapped in a togglable ``<g class="lv-frame"
    data-path=...>`` group by render/scene.py — a Case, (Conditional/Diagram-)
    Disable, or Event structure, or a STACKED sequence. MUST stay in lockstep
    with scene.py's own interactive-structure set, so a location's step matches
    a real hideable group the viewer can address. A While/For loop shows its body
    unconditionally, and a FLAT sequence shows every frame at once (film-strip)
    — neither is hidden, so their children keep the enclosing context's
    locality unchanged."""
    if isinstance(op, (CaseStructureNode, DisableStructureNode, EventStructureNode)):
        return True
    return isinstance(op, SequenceNode) and op.node_type != "flatSequence"


def frame_display(frame: Frame, op: AnyGraphNode) -> str:
    """Human label for one frame — the SAME faithful, enum-aware text the
    netlist/tree produces. A case frame goes through ``op_walk._selector_label``
    (resolving the owning structure's selector ``lv_type`` exactly as
    ``netlist_build._build_case_scope_gn`` does, so an enum value reads as its
    item name, an error cluster as ``No Error``/``Error``, etc.); a sequence
    frame is its index."""
    if isinstance(op, DisableStructureNode) and isinstance(frame, CaseFrame):
        # Disable frames ARE CaseFrames, but their selector_value already IS the
        # display text ("Enabled"/"Disabled"/"Frame N") and their is_default
        # means "the active/compiled-in frame", not a catch-all default — so
        # they must NOT go through _selector_label's is_default→"Default" branch
        # (mirror ``_build_disabled_scope_gn``, which bypasses it for the same
        # reason).
        return str(frame.selector_value)
    if isinstance(frame, CaseFrame):
        if isinstance(op, CaseStructureNode):
            return case_frame_label_gn(op, frame)
        return _selector_label(frame, None, False)
    if isinstance(frame, SequenceFrame):
        return str(frame.index)
    if isinstance(frame, EventFrame):
        return frame.event_label or "event frame"
    return "frame"


def _frame_shown(op: AnyGraphNode, frame: Frame) -> tuple[str, ...]:
    """The value(s) ``frame`` of the structure ``op`` shows: a case frame's
    selector values (and ``Default`` when its label says so), else the frame's
    one label (``frame_display``)."""
    if isinstance(op, CaseStructureNode) and isinstance(frame, CaseFrame):
        lv_type, is_error = case_selector_type_gn(op)
        return selector_shown_values(frame, lv_type, is_error)
    return (frame_display(frame, op),)


@dataclass(frozen=True)
class FrameStep:
    """One frame an element is inside: frame ``frame`` (its position in the
    structure's frames) of the structure whose uid is ``structure``, and the
    value(s) that frame shows."""

    structure: str
    frame: int
    values: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "structure": self.structure,
            "frame": self.frame,
            "value": list(self.values),
        }


# Root -> leaf: every frame an element is inside.
Location = tuple[FrameStep, ...]


def location_to_json(location: Location | None) -> list[dict[str, object]] | None:
    return None if location is None else [step.to_dict() for step in location]


def frame_step(op: AnyGraphNode, position: int) -> FrameStep:
    """The location step for frame ``position`` of the structure ``op``."""
    frames = frames_of(op) or []
    return FrameStep(uid_of(op.id), position, _frame_shown(op, frames[position]))


def frame_location(op: AnyGraphNode, outer: Location, position: int) -> Location:
    """Where frame ``position`` of the structure ``op`` (itself at ``outer``)
    sits: ``outer``, then -- ONLY when the structure is interactive (see
    ``is_interactive_struct``) -- that frame itself. Those are the only kinds
    with a real rendered ``lv-frame`` group a viewer could address; a flat
    sequence's frame keeps its structure's location."""
    if is_interactive_struct(op):
        return (*outer, frame_step(op, position))
    return outer


@dataclass(frozen=True)
class FramePairs:
    """The frames of one structure that are the same frame in both versions:
    the structure's uid in each version (they differ when LabVIEW gave the
    structure a new uid and the diff matched it by dataflow) and every pair as
    ``(before position, after position)`` -- matched by value, then by content
    (``match_frames``). An added or removed frame has no pair; a matched
    structure whose frames all changed has none at all."""

    before: str
    after: str
    pairs: list[tuple[int, int]]


def frame_match_key(frame: Frame) -> str:
    """Stable key for matching a frame across versions: LabVIEW's own frame
    ``uid`` when the parser recorded one (currently only ``SequenceFrame`` —
    see parser/nodes/sequence.py; a ``CaseFrame``'s heap format carries no
    per-frame uid, so the parser never sets one — always None today). Falls
    back to the frame's selector/index VALUE, which DEGRADES a genuine in-place
    value edit (e.g. LabVIEW's "add value to this case") into a remove+add,
    because the frame's identity is then indistinguishable from its value.
    Documented, not hidden — a real frame uid always wins when present.

    An ``EventFrame`` has no uid AND a RESOLUTION-dependent label (the displayed
    frame reconstructs faithfully; others degrade to ``<unknown event ...>``), so
    keying on its label would make a mere relabel read as remove+add. Key on the
    stable leading ``[N]`` index instead."""
    if frame.uid is not None:
        return frame.uid
    if isinstance(frame, EventFrame):
        s = frame.event_label.lstrip()
        if s.startswith("[") and "]" in s:
            idx = s[1 : s.index("]")]
            if idx.isdigit():
                return f"~[{idx}]"
        return f"~{frame.event_label}"
    if isinstance(frame, SequenceFrame):
        return f"~{frame.index}"
    if isinstance(frame, CaseFrame):
        return f"~{frame.selector_value}"
    return "~"


# A frame's cross-version match key: its ``frame_match_key`` and how many earlier
# frames of its structure have that key (two frames with one value -- a VI
# lvkit could not read -- still match one to one, in order).
_MatchKey = tuple[str, int]


def _match_keys(frames: Sequence[Frame]) -> dict[_MatchKey, int]:
    """Each frame's ``_MatchKey`` -> its position."""
    seen: dict[str, int] = {}
    keys: dict[_MatchKey, int] = {}
    for position, frame in enumerate(frames):
        key = frame_match_key(frame)
        keys[(key, seen.get(key, 0))] = position
        seen[key] = seen.get(key, 0) + 1
    return keys


def _match_key_sort(key: _MatchKey) -> tuple[tuple[int, object], int]:
    return (uid_sort(key[0]), key[1])


def frame_value_changed(fa: Frame, fb: Frame) -> bool:
    """Whether two frames MATCHED by ``frame_match_key`` differ in their selector/
    index content — the ``kind="value"`` detector. When matched by real uid
    (sequence frames) this catches e.g. a stacked-sequence reorder or a
    case's ``is_default``/ranges/strings changing while the uid stayed put;
    when matched by the value-based fallback key, the key ITSELF encodes
    ``selector_value``/``index``, so those are equal by construction and only
    the remaining fields (``is_default``, ranges, strings) can differ."""
    if isinstance(fa, CaseFrame) and isinstance(fb, CaseFrame):
        return (
            fa.selector_value != fb.selector_value
            or fa.is_default != fb.is_default
            or fa.selector_ranges != fb.selector_ranges
            or fa.selector_strings != fb.selector_strings
        )
    if isinstance(fa, SequenceFrame) and isinstance(fb, SequenceFrame):
        return fa.index != fb.index
    # Event frames are matched by their stable [N] index (see frame_match_key); the
    # label is resolution-dependent, so a label-only diff is our text changing,
    # not the VI's event — never a frame value change.
    return False


@dataclass(frozen=True)
class FrameMatch:
    """How one structure's frames match across versions (positions):
    ``by_key`` matched by value (or LabVIEW's own frame uid), ``by_content``
    the same frame under a changed value, and the frames only one version
    has."""

    by_key: list[tuple[int, int]]
    by_content: list[tuple[int, int]]
    removed: list[int]
    added: list[int]

    @property
    def pairs(self) -> list[tuple[int, int]]:
        """Every pair, in after-position order."""
        return sorted([*self.by_key, *self.by_content], key=lambda pair: pair[1])


def match_frames(
    frames_a: Sequence[Frame],
    frames_b: Sequence[Frame],
    content_a: Callable[[int], set[str]],
    content_b: Callable[[int], set[str]],
    matchmap: Mapping[str, str],
) -> FrameMatch:
    """Match two versions' frames: by key first, then leftover frames by shared
    CONTENT — a before frame's node uids (``content_a``), mapped through the
    dataflow match map (``base->head``; identity when a node kept its uid),
    overlapping an after frame's (``content_b``). Greedy by most shared nodes
    with the positions as the tiebreak; each frame used at most once. Zero
    overlap means a genuine add/remove."""
    map_a, map_b = _match_keys(frames_a), _match_keys(frames_b)
    by_key = [
        (map_a[k], map_b[k])
        for k in sorted(map_a.keys() & map_b.keys(), key=_match_key_sort)
    ]
    only_a = [
        map_a[k] for k in sorted(map_a.keys() - map_b.keys(), key=_match_key_sort)
    ]
    only_b = [
        map_b[k] for k in sorted(map_b.keys() - map_a.keys(), key=_match_key_sort)
    ]
    contents_b = {pb: content_b(pb) for pb in only_b}
    scored: list[tuple[int, int, int]] = []
    for pa in only_a:
        a_uids = {matchmap.get(u, u) for u in content_a(pa)}
        for pb in only_b:
            shared = len(a_uids & contents_b[pb])
            if shared:
                scored.append((shared, pa, pb))
    scored.sort(key=lambda s: (-s[0], s[1], s[2]))
    used_a: set[int] = set()
    used_b: set[int] = set()
    by_content: list[tuple[int, int]] = []
    for _, pa, pb in scored:
        if pa in used_a or pb in used_b:
            continue
        used_a.add(pa)
        used_b.add(pb)
        by_content.append((pa, pb))
    return FrameMatch(
        by_key,
        by_content,
        [pa for pa in only_a if pa not in used_a],
        [pb for pb in only_b if pb not in used_b],
    )


class FrameMatching:
    """Every matched structure's ``FramePairs``, as a map from the before
    version onto the after version."""

    def __init__(self, frame_pairs: Sequence[FramePairs]) -> None:
        self._after: dict[str, tuple[str, dict[int, int]]] = {
            fp.before: (fp.after, dict(fp.pairs)) for fp in frame_pairs
        }

    def after_structure(self, before_uid: str) -> str | None:
        """The after uid of a matched structure; None for a before-only one."""
        found = self._after.get(before_uid)
        return found[0] if found is not None else None

    def after_frame(self, before_uid: str, position: int) -> int | None:
        """The after position of the same frame; None when it has no pair."""
        found = self._after.get(before_uid)
        return found[1].get(position) if found is not None else None

    def before_key(self, location: Location) -> tuple[object, ...]:
        """A before-version location, as the after version addresses it --
        each frame as its after structure and partner position. A frame with no
        partner keeps its before address, so it equals no after location."""
        key: list[object] = []
        for step in location:
            partner = self.after_frame(step.structure, step.frame)
            after = self.after_structure(step.structure)
            key.append(
                (after, partner)
                if after is not None and partner is not None
                else ("before", step.structure, step.frame)
            )
        return tuple(key)

    @staticmethod
    def after_key(location: Location) -> tuple[object, ...]:
        """An after-version location, comparable with ``before_key``."""
        return tuple((step.structure, step.frame) for step in location)
