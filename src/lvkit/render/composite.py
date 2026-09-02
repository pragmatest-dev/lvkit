"""The composite render tree: the ``RenderObject`` hierarchy + builder + the
single ``draw_scene`` entry.

DIRECTIVE 1 — starting at the block-diagram root, recursively tell nodes,
structures and wires to draw themselves in zPlaneList order. One root, one
recursive ``draw()`` walk. A structure draws its OPAQUE body first (so a later
sibling occludes an earlier sibling's whole subtree — the #35/#39 fix), then its
own inner wires, then its children in paint order, then its border terminals
last; an INTERACTIVE structure (case / stacked-sequence / disable / event) is a
tree object that recurses its FRAMES inside ``lv-frame`` groups.

Containment (who is inside whom) comes from the graph, carried on each view
model as ``node.parent`` / ``RenderWireNet.container_uid``. Paint order comes
from the layout, carried as ``Scene.z_order``. This module is the composite; it
reuses the leaf pixel helpers in ``draw`` and the leaf ``Glyph`` faces (resolved
onto each ``RenderNode``) — it never re-implements them. A ``Glyph`` is a
draw-only leaf FACE stamped into a rect; a ``RenderObject`` is a placed tree
citizen that draws itself and (for a container) recurses its children.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..graph.models import (
    CaseStructureNode,
    DisableStructureNode,
    EventStructureNode,
    SequenceNode,
)
from .backend import Backend, Point
from .draw import (
    _draw_border_terminal,
    _draw_layer_coercion_dots,
    draw_fp_terminal,
    draw_help_overlay,
    draw_node,
)
from .glyphs.structures.base import ERROR_BORDER_W
from .glyphs.structures.factory import structure_body_glyph
from .glyphs.structures.selectable import SelectableStructureGlyph, SelectorState
from .scene import (
    RenderDecoration,
    RenderFPTerminal,
    RenderNode,
    RenderStructure,
    RenderWireNet,
    Scene,
    _is_default_visible,
    encode_frame_path,
)
from .style import DEFAULT_THEME, Theme, wire_dasharray


def _raw(qualified: str | None) -> str | None:
    """Raw (path-prefix-stripped) UID of a qualified ``vi_key::UID`` id — the
    key shared by ``Scene.z_order``, ``RenderStructure.raw_uid`` and
    ``RenderWireNet.container_uid``."""
    return qualified.rsplit("::", 1)[-1] if qualified else None


def _is_interactive_structure(node: object) -> bool:
    """Which structure kinds get selector chrome + ``lv-frame``/``lv-selector``
    groups: a case, any disable-family structure, an event structure, and a
    STACKED sequence (a flat sequence shows every frame, so it is not paged)."""
    return isinstance(
        node, (CaseStructureNode, DisableStructureNode, EventStructureNode)
    ) or (isinstance(node, SequenceNode) and node.node_type != "flatSequence")


def _error_border_color(
    scene: Scene, raw_uid: str, value: str, theme: Theme
) -> str | None:
    """Green (No Error) / red (Error) border colour for an error-cluster case's
    shown frame, or ``None`` if this structure isn't an error case."""
    err = scene.error_frame_no_error.get(raw_uid)
    if err is None:
        return None
    return (
        theme.case_no_error_border if err.get(value, False) else theme.case_error_border
    )


def _selector_state(rs: RenderStructure, scene: Scene) -> SelectorState:
    """Resolve the scene's frame data for one interactive structure into the
    plain :class:`SelectorState` the glyph's selector methods consume — folding
    the sequence ``N [0..M]`` label form and the case's typed labels here so the
    glyph stays pure."""
    raw = rs.raw_uid
    values = scene.frame_values.get(raw, [])
    default = scene.default_frame.get(raw) or ""
    if isinstance(rs.node, SequenceNode):  # flat isn't interactive
        last = len(values) - 1 if values else 0
        display = {v: f"{v} [0..{last}]" for v in values}
    else:
        labels = scene.frame_labels.get(raw, {})
        display = {v: labels.get(v, v) for v in values}
    return SelectorState(raw_uid=raw, values=values, default=default, display=display)


def _draw_wire_nets(nets: list[RenderWireNet], backend: Backend, theme: Theme) -> None:
    """One diagram's wires: per-net casing (canvas halo) then colour then
    junctions — so a net's own trunk stays solid while the NEXT net's casing
    breaks the prior net's colour at an orthogonal crossing.

    Everything a wire draws — its width, dash, and (for a class) its two-band
    edge/core border — is read off ``net.style`` (the one WireStyle); a flat wire
    has no ``edge_color`` and draws as a single stroke."""
    casing = theme.wire_casing
    for net in nets:
        style = net.style
        dash = wire_dasharray(style)  # solid for a class pen (line style is solid)
        if casing > 0:
            for branch in net.branches:
                backend.path(
                    branch,
                    stroke=theme.canvas,
                    stroke_width=style.width + 2 * casing,
                )
        for branch in net.branches:
            if style.edge_color is not None and style.core_width is not None:
                # Two-band class wire: solid outer edge band, then the narrower
                # center on top.
                backend.path(branch, stroke=style.edge_color, stroke_width=style.width)
                if style.fill_pattern:
                    # The class fill pattern is the actual bitmap tiled along the
                    # wire in the center color (the "chain"), over the edge band.
                    _draw_wire_pattern(
                        branch, backend, style.fill_pattern, style.color, style.width
                    )
                else:
                    backend.path(
                        branch, stroke=style.color, stroke_width=style.core_width
                    )
            elif style.fill_pattern:
                _draw_wire_pattern(
                    branch, backend, style.fill_pattern, style.color, style.width
                )
            else:
                backend.path(
                    branch,
                    stroke=style.color,
                    stroke_width=style.width,
                    stroke_dasharray=dash,
                )
        for jx, jy in net.junctions:
            backend.circle(jx, jy, 3.0, fill=style.color)


def _draw_wire_pattern(
    branch: list[Point],
    backend: Backend,
    rows: tuple[int, ...],
    color: str,
    width: float,
) -> None:
    """Draw the class wire's actual fill-pattern bitmap tiled along the wire — the
    real texture (chain/dash/bars) from the ``.lvclass`` bitmap, in the pen color,
    NOT an approximated dash. The 8-row bitmap is scaled to the wire's ``width``
    (each row a ``width/8`` band, each column a ``width/8`` step along the length)
    and tiled along each axis-aligned segment, oriented per horizontal/vertical
    run — a set bit is a filled cell, a clear bit lets the edge band show
    through."""
    n = len(rows)
    if n == 0:
        return
    cell = width / n
    for (x0, y0), (x1, y1) in zip(branch, branch[1:], strict=False):
        horizontal = abs(x1 - x0) >= abs(y1 - y0)
        a0, a1 = (x0, x1) if horizontal else (y0, y1)
        lo, hi = min(a0, a1), max(a0, a1)
        cross = (y0 + y1) / 2 if horizontal else (x0 + x1) / 2
        t = lo
        while t < hi:
            for r, byte in enumerate(rows):
                for c in range(8):
                    if not (byte >> (7 - c)) & 1:
                        continue
                    along = t + c * cell
                    if along >= hi:
                        continue
                    perp = cross - width / 2 + r * cell
                    bx, by = (along, perp) if horizontal else (perp, along)
                    backend.rect(
                        bx, by, bx + cell, by + cell, fill=color, stroke="none"
                    )
            t += 8 * cell


class RenderObject(ABC):
    """A placed citizen of the composite tree that knows how to draw itself
    (and, for a container, recurse into its children)."""

    @abstractmethod
    def draw(self, backend: Backend, theme: Theme) -> None: ...


@dataclass
class DiagramContent:
    """The drawable content of ONE diagram — the root, a loop/IPES/flat-sequence
    body, or one interactive frame: wires behind, then z-ordered children, then
    front-panel terminals and coercion dots. Not a container border itself."""

    nets: list[RenderWireNet] = field(default_factory=list)
    children: list[RenderObject] = field(default_factory=list)
    fps: list[RenderFPTerminal] = field(default_factory=list)
    dots: list[Point] = field(default_factory=list)

    def draw(self, backend: Backend, theme: Theme) -> None:
        _draw_wire_nets(self.nets, backend, theme)  # behind
        for child in self.children:  # zPlaneList paint order
            child.draw(backend, theme)
        for fp in self.fps:
            draw_fp_terminal(
                fp.terminal,
                fp.bounds,
                backend,
                theme,
                fp.label_visible,
                fp.label_bounds,
            )
        _draw_layer_coercion_dots(self.nets, self.dots, backend, theme)


@dataclass
class NodeObject(RenderObject):
    """A leaf diagram object (primitive, SubVI, constant, …). Draws its resolved
    ``Glyph`` face at its own bounds."""

    rn: RenderNode

    def draw(self, backend: Backend, theme: Theme) -> None:
        draw_node(self.rn, backend, theme)


@dataclass
class DecorationObject(RenderObject):
    """A block-diagram decoration (Flat Frame / Line / Arrow). A pure-visual leaf
    that paints its clean-room shape glyph at its bounds; interleaves with nodes
    by z-order like any other diagram citizen."""

    deco: RenderDecoration

    def draw(self, backend: Backend, theme: Theme) -> None:
        self.deco.glyph.draw(backend, self.deco.bounds, theme)


@dataclass
class StructureObject(RenderObject):
    """A structure and its inner diagram(s). Draws its opaque body + outline
    first, then (non-interactive) its single body diagram, or (interactive) one
    ``lv-frame`` group per frame — recursing the whole subtree each time."""

    rs: RenderStructure
    scene: Scene
    interactive: bool
    body: DiagramContent | None  # non-interactive
    frames: list[tuple[str, DiagramContent]]  # interactive: (frame value, content)

    def _glyph(self, theme: Theme):
        rs = self.rs
        default = self.scene.default_frame.get(rs.raw_uid, "")
        border_color = _error_border_color(self.scene, rs.raw_uid, default, theme)
        # A disable-family structure routes to its per-subtype glyph by kind
        # (Type Specialization → solid + icon; the rest → dotted); #31/#46.
        disable_kind = (
            rs.node.kind if isinstance(rs.node, DisableStructureNode) else None
        )
        return structure_body_glyph(
            rs.node.node_type,
            border_color=border_color,
            disable_kind=disable_kind,
            case_insensitive=bool(getattr(rs.node, "case_insensitive", False)),
            dividers=rs.dividers,
        )

    def draw(self, backend: Backend, theme: Theme) -> None:
        rs = self.rs
        # Background: opaque body + outline — occludes any earlier sibling's
        # whole subtree (the #35/#39 fix); its OWN inner wires draw on top next.
        glyph = self._glyph(theme)
        glyph.draw(backend, rs.bounds, theme)
        # Contents clip to the glyph's INTERIOR (the front card for a For loop,
        # the whole bounds for every other kind), pulled inside the border by
        # half its stroke width so content meets the border stroke's inner edge
        # flush — no overpaint, no clearance gap.
        clip = glyph.interior(rs.bounds)
        if self.interactive:
            assert isinstance(glyph, SelectableStructureGlyph)
            self._draw_interactive(backend, theme, clip, glyph)
        else:
            assert self.body is not None
            backend.begin_group(clip=clip)
            self.body.draw(backend, theme)
            backend.end_group()
            # Border terminals LAST (a tunnel sits on its wire), default frame,
            # UNCLIPPED so a glyph sitting on the edge is never shaved.
            fv = self.scene.default_frame.get(rs.raw_uid)
            for bt in rs.border_terminals:
                _draw_border_terminal(bt, backend, theme, fv)

    def _draw_interactive(
        self,
        backend: Backend,
        theme: Theme,
        clip: tuple[float, float, float, float],
        glyph: SelectableStructureGlyph,
    ) -> None:
        rs, scene = self.rs, self.scene
        state = _selector_state(rs, scene)
        glyph.draw_selector(backend, rs.bounds, theme, state)  # base selector chrome
        default = scene.default_frame.get(rs.raw_uid)
        # Base (always-present) border terminals in the default state; each
        # frame group redraws them for its own value on top.
        for bt in rs.border_terminals:
            _draw_border_terminal(bt, backend, theme, default)
        for value, content in self.frames:
            path = rs.frame_path + ((rs.raw_uid, value),)
            visible = _is_default_visible(path, scene.default_frame)
            backend.begin_group(
                cls="lv-frame" if visible else "lv-frame lv-frame-hidden",
                data={"path": encode_frame_path(path)},
            )
            # Clip the frame's inner content to the glyph interior (a nested
            # structure whose box exceeds this one doesn't spill out); border
            # terminals stay UNCLIPPED so an edge-seated glyph isn't shaved.
            backend.begin_group(clip=clip)
            content.draw(backend, theme)
            backend.end_group()
            # The container draws its tunnels ON TOP of this frame's inner wires,
            # for THIS frame value (an output tunnel unwired here shows a hole).
            for bt in rs.border_terminals:
                _draw_border_terminal(bt, backend, theme, value)
            # Disable structure: wash every non-enabled frame with a translucent
            # grey mask (matches LabVIEW's inactive look); redraw the selector on
            # top so its chrome stays crisp.
            if isinstance(rs.node, DisableStructureNode) and value != default:
                x1, y1, x2, y2 = rs.bounds
                backend.begin_group(cls="lv-disabled-mask")
                backend.rect(x1, y1, x2, y2, fill=theme.disabled_mask)
                backend.end_group()
                glyph.draw_selector(backend, rs.bounds, theme, state)
            backend.end_group()
        self._draw_value_labels(backend, theme, glyph, state)

    def _draw_value_labels(
        self,
        backend: Backend,
        theme: Theme,
        glyph: SelectableStructureGlyph,
        state: SelectorState,
    ) -> None:
        rs, scene = self.rs, self.scene
        default = scene.default_frame.get(rs.raw_uid)
        for value in scene.frame_values.get(rs.raw_uid, []):
            label_path = rs.frame_path + ((rs.raw_uid, value),)
            visible = value == default and _is_default_visible(
                rs.frame_path, scene.default_frame
            )
            backend.begin_group(
                cls="lv-frame lv-label"
                if visible
                else "lv-frame lv-label lv-frame-hidden",
                data={"path": encode_frame_path(label_path)},
            )
            glyph.draw_value_label(backend, rs.bounds, theme, state, value)
            err_color = _error_border_color(scene, rs.raw_uid, value, theme)
            if err_color is not None:
                bx1, by1, bx2, by2 = rs.bounds
                backend.rect(
                    bx1,
                    by1,
                    bx2,
                    by2,
                    fill="none",
                    stroke=err_color,
                    stroke_width=ERROR_BORDER_W,
                )
            backend.end_group()

    def draw_menu(self, backend: Backend, theme: Theme) -> None:
        """Dropdown menu overlay — drawn LAST of all so it sits over the whole
        diagram when opened (display:none until its ▼ toggle shows it)."""
        if self.interactive and self.scene.frame_values.get(self.rs.raw_uid):
            glyph = self._glyph(theme)
            assert isinstance(glyph, SelectableStructureGlyph)
            glyph.draw_menu(
                backend, self.rs.bounds, theme, _selector_state(self.rs, self.scene)
            )


@dataclass
class DiagramObject(RenderObject):
    """The block-diagram root diagram. ``draw()`` is the SINGLE entry: it draws
    the root content (recursing the whole tree), then the two overlay passes
    that must sit above everything — the case/sequence dropdown menus and the
    connector-help hover panels."""

    content: DiagramContent
    interactive_structures: list[StructureObject]
    all_nodes: list[RenderNode]

    def draw(self, backend: Backend, theme: Theme) -> None:
        self.content.draw(backend, theme)
        # Overlays (top of z-order), NOT the main composite draw loop:
        for se in self.interactive_structures:
            se.draw_menu(backend, theme)
        draw_help_overlay(self.all_nodes, backend, theme)


def build_render_tree(scene: Scene) -> DiagramObject:
    """Assemble the composite tree from the scene view model.

    Containment nests every object (loops included, which create no frame
    path); paint order (``Scene.z_order``) orders siblings; interactive
    structures split their children by frame value.
    """
    z = scene.z_order

    def rank(raw_uid: str) -> int:
        # zPlaneList document index (0 = frontmost). Unknown → -1 so it sorts
        # last under the reverse (back-to-front) draw sort → drawn frontmost.
        return z.get(raw_uid, -1)

    # Group view-model items by their containment owner (raw structure uid, or
    # None for the root diagram).
    nodes_by_container: dict[str | None, list[RenderNode]] = {}
    for rn in scene.nodes:
        nodes_by_container.setdefault(_raw(rn.node.parent), []).append(rn)
    structs_by_container: dict[str | None, list[RenderStructure]] = {}
    for rs in scene.structures:
        structs_by_container.setdefault(_raw(rs.node.parent), []).append(rs)
    nets_by_container: dict[str | None, list[RenderWireNet]] = {}
    for net in scene.wire_nets:
        nets_by_container.setdefault(net.container_uid, []).append(net)
    decos_by_container: dict[str | None, list[RenderDecoration]] = {}
    for deco in scene.decorations:
        decos_by_container.setdefault(deco.container_uid, []).append(deco)

    # Front-panel terminals + arithmetic coercion dots key off frame_path (their
    # innermost interactive ancestor); () → root diagram.
    fps_by_cf: dict[tuple[str | None, str | None], list[RenderFPTerminal]] = {}
    for fp in scene.fp_terminals:
        fkey = fp.frame_path[-1] if fp.frame_path else (None, None)
        fps_by_cf.setdefault(fkey, []).append(fp)
    dots_by_cf: dict[tuple[str | None, str | None], list[Point]] = {}
    for d in scene.coercion_dots:
        dkey = d.frame_path[-1] if d.frame_path else (None, None)
        dots_by_cf.setdefault(dkey, []).append(d.point)

    interactive_structures: list[StructureObject] = []

    def _node_frame(rn: RenderNode) -> str | None:
        return str(rn.node.frame) if rn.node.frame is not None else None

    def _struct_frame(rs: RenderStructure) -> str | None:
        return str(rs.node.frame) if rs.node.frame is not None else None

    def _wire_frame(net: RenderWireNet, container: str | None) -> str | None:
        for s, v in net.frame_path:
            if s == container:
                return v
        return None

    def build_content(
        container: str | None, interactive: bool, frame: str | None
    ) -> DiagramContent:
        # z-ordered children (nodes + nested structures) for this diagram.
        ranked: list[tuple[int, RenderObject]] = []
        for rn in nodes_by_container.get(container, []):
            if interactive and _node_frame(rn) != frame:
                continue
            ranked.append((rank(rn.dom_id), NodeObject(rn)))
        for rs in structs_by_container.get(container, []):
            if interactive and _struct_frame(rs) != frame:
                continue
            ranked.append((rank(rs.raw_uid), _build_structure(rs)))
        # Decorations (Flat Frame / Line / Arrow) interleave by z-order like
        # nodes. A decoration carries no frame value yet, so inside an
        # interactive container it draws in the DEFAULT frame only (shown once,
        # not duplicated across frames) — frame-precise placement is a follow-up.
        deco_frame_ok = (not interactive) or frame == scene.default_frame.get(
            container or "", frame
        )
        if deco_frame_ok:
            for deco in decos_by_container.get(container, []):
                ranked.append((rank(deco.dom_id), DecorationObject(deco)))
        # LabVIEW's zPlaneList is FRONT-to-back: document index 0 (lowest
        # z_order rank) is the FRONTMOST object, drawn LAST so it occludes.
        # So draw back-to-front = highest rank first (reverse=True). Ties keep
        # insertion order (deterministic). Unknown-rank items (no geometry
        # entry, rank -1) sort last -> drawn frontmost, a safe visible default.
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        children = [obj for _, obj in ranked]

        nets = [
            net
            for net in nets_by_container.get(container, [])
            if not interactive or _wire_frame(net, container) == frame
        ]

        # Draw wires in LabVIEW's signalList paint order (Scene.wire_z, keyed by
        # source terminal). Like zPlaneList, signalList is FRONT-to-back: a LOWER
        # rank is more forward, so it must draw LAST and its casing breaks the
        # wires behind it at a crossing. Descending sort (highest rank first).
        # Verified against the #39 reference: the String wire (lower rank) is on
        # top of the y wire. Unknown rank (-1) sorts last (drawn frontmost).
        def _net_rank(net: RenderWireNet) -> int:
            src = net.source
            if src is None:
                return -1
            raw = _raw(src.source.terminal_id)
            return scene.wire_z.get(raw, -1) if raw is not None else -1

        nets.sort(key=_net_rank, reverse=True)
        return DiagramContent(
            nets=nets,
            children=children,
            fps=fps_by_cf.get((container, frame), []),
            dots=dots_by_cf.get((container, frame), []),
        )

    def _build_structure(rs: RenderStructure) -> StructureObject:
        if _is_interactive_structure(rs.node):
            frames = [
                (value, build_content(rs.raw_uid, True, value))
                for value in scene.frame_values.get(rs.raw_uid, [])
            ]
            se = StructureObject(rs, scene, True, None, frames)
            interactive_structures.append(se)
            return se
        return StructureObject(
            rs, scene, False, build_content(rs.raw_uid, False, None), []
        )

    root_content = build_content(None, False, None)
    return DiagramObject(root_content, interactive_structures, scene.nodes)


def draw_scene(scene: Scene, backend: Backend, theme: Theme = DEFAULT_THEME) -> None:
    """Draw a whole scene: paint the canvas, then the single recursive
    ``root.draw()`` walk (which also emits the menu + help overlays last).
    There is NO flat scene-list draw loop here — the composite tree owns the
    entire draw order."""
    x1, y1, x2, y2 = scene.bounds
    backend.rect(x1, y1, x2, y2, fill=theme.canvas)
    root = build_render_tree(scene)
    root.draw(backend, theme)
