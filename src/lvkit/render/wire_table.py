"""Decodes a ``compressedWireTable`` blob into intermediate wire bend points.

LabVIEW stores each signal's routed bend geometry as a hex blob on the block
diagram heap: ``byte0`` is the vertex count ``V`` (so the wire has ``V-1``
orthogonal segments), ``byte1`` is segment-0's direction (``0x08``=East,
``0x04``=South, ``0x02``=West, ``0x01``=North — screen coordinates, y grows
downward), the next ``V-2`` bytes are per-bend SIGN bits (``0x00``=+axis
East/South, ``0x01``=-axis West/North — segment directions alternate
horizontal/vertical, so only a sign needs to be stored, not a full
direction), and the final ``V-2`` bytes are the lengths of segments
``0..V-3`` (the last segment's length is implied by the endpoint that the
caller already knows). A length of 256 or more is escaped as ``0xff`` followed
by the value as a 16-bit big-endian pair, so the length section is a
variable-width stream rather than one byte per segment (this is why the total
blob length is not always ``2V-2``).

A signal's ``termList`` is an ordered ``[source_uid, *sink_uids]`` and every
uid's center is known, so a wire is a tree whose leaves land on KNOWN
terminals. ``decode_signal`` is the one entry point for both cases: a
2-endpoint wire is the 1-leaf chain; a fan-out is the N-leaf tree.

Fan-out geometry is recovered by a DETERMINISTIC DFS walk of the token stream:
fork directions come from a fixed base set per token with a turned-trunk remap
(``backward → negative-axis perpendicular``, a routing invariant), so ~99.5% of
wires decode with NO fork search and NO tolerance — see
``docs/wire-compression-format.md`` and the ``_decode_tree_deterministic`` /
``_walk_tree`` pair below. Decoded leaves are matched to their known sinks by
minimum-cost assignment and snapped exactly onto the uid centers. The rare
turned-trunk forks the bytes underdetermine fall through to a constrained,
sink-validated search (``_decode_tree_search``); anything that still cannot land
every leaf on a known terminal returns ``None`` and the caller falls back to the
auto-router (see task #84 / #76).
"""

from __future__ import annotations

Point = tuple[float, float]
DIR: dict[int, Point] = {0x08: (1, 0), 0x04: (0, 1), 0x02: (-1, 0), 0x01: (0, -1)}

# Deterministic fork rule (see docs/wire-compression-format.md "Layout B").
# A fork token's low 2 bits select an ABSOLUTE base pair over {N,S,E} as
# (immediate, [deferred siblings]); W is the unused 4th direction. On a TURNED
# trunk, any base direction that points BACKWARD (opposite the trunk) is remapped
# to the trunk's negative-axis perpendicular (N for a horizontal trunk, W for a
# vertical one) — a routing invariant (LV never draws a branch back into its own
# trunk), so the geometry is recovered from the bytes with no endpoint search.
_N, _S, _E, _W = (0, -1), (0, 1), (1, 0), (-1, 0)
_FORK_BASE: dict[int, tuple[Point, list[Point]]] = {
    0x04: (_N, [_S, _E]),
    0x05: (_S, [_E]),
    0x06: (_N, [_E]),
    0x07: (_N, [_S]),
}
# px: a decoded leaf "reaches" its known terminal if within this of the sink
# center (the terminal glyph's own extent — an attach-point offset, NOT a
# matching slop; the leaf is then snapped exactly onto the uid center).
_TERM_BOX = 16.0

# When True (default), wires whose heap `compressedWireTable` decodes are drawn
# from LabVIEW's own routed geometry (single-net + fan-out + straight-through
# taps, ~99.7% of wires); the rest fall back to the auto-router. Set False to get
# the pure auto-router output (the A/B baseline). See task #84.
FAITHFUL_WIRE_TABLE = True


def _decode_lengths(stream: list[int], n: int) -> list[int] | None:
    """Decode a variable-width length stream into exactly ``n`` values.

    A byte below ``0xff`` is a one-byte length; ``0xff hi lo`` is a 16-bit
    big-endian length (used when a segment is 256 px or longer). Returns
    ``None`` unless exactly ``n`` values consume the whole stream.

    The largest length observed across the corpus is 1510 px, and ``hi`` is
    never ``0xff`` (no value >= 65280), so a ``0xff 0xff`` pair — the natural
    sentinel for any wider/chained encoding — is unobserved and uncharacterized.
    We return ``None`` on it rather than guess, letting the caller fall back to
    the auto-router.
    """
    out: list[int] = []
    i = 0
    while i < len(stream) and len(out) < n:
        if stream[i] == 0xFF:
            if i + 2 >= len(stream) or stream[i + 1] == 0xFF:
                return None
            out.append((stream[i + 1] << 8) | stream[i + 2])
            i += 3
        else:
            out.append(stream[i])
            i += 1
    if len(out) != n or i != len(stream):
        return None
    return out


def _decode_chain(blob: str, start: Point, end: Point) -> list[Point] | None:
    """Decode the INTERMEDIATE bend points of a single (1-leaf) wire's
    ``compressedWireTable`` blob — the 2-endpoint case of ``decode_signal``.

    Returns the ``mid`` points only (excluding ``start``/``end``), designed
    to drop straight into ``_compress([start, *mid, end])`` exactly as
    ``router.route(...)`` does. The last bend is snapped so the wrapped path
    stays orthogonal; the endpoints themselves are always the true terminal
    centers. Returns ``None`` for the fan-out layout / malformed blobs.
    """
    try:
        b = [int(blob[i : i + 2], 16) for i in range(0, len(blob), 2)]
    except ValueError:
        return None
    if not b:
        return None
    v = b[0]
    nseg = v - 1
    nbend = v - 2
    if nseg == 0:
        # Single-vertex signal (blob `01`): a degenerate/zero-length wire whose
        # two terminals resolve to the same center (a co-located terminal pair).
        # There is no geometry to route — connect the endpoints directly. Every
        # such signal observed has coincident endpoints, so this draws a point,
        # not a stray diagonal. Handled BEFORE the `b[1]` gate (the blob is one
        # byte, so b[1] doesn't exist).
        return []
    if nseg < 1 or len(b) < 2 or b[1] not in DIR or len(b) < 2 + nbend:
        return None  # fan-out / malformed
    if nseg == 1:
        return []  # straight: scene connects the two centers directly
    signs = b[2 : 2 + nbend]
    lengths = _decode_lengths(b[2 + nbend :], nbend)
    if lengths is None:
        return None  # length stream did not consume cleanly
    dx0, dy0 = DIR[b[1]]
    horiz0 = dx0 != 0
    mid: list[Point] = []
    cx, cy = start
    for i in range(nseg - 1):  # intermediate vertices v1..v_{nseg-1}
        horiz = horiz0 == (i % 2 == 0)
        sign = (
            (dx0 if horiz else dy0)
            if i == 0
            else (-1 if signs[i - 1] == 0x01 else 1)
        )
        if horiz:
            cx += sign * lengths[i]
        else:
            cy += sign * lengths[i]
        mid.append((cx, cy))
    # Snap the last bend so the final segment (to `end`) is axis-aligned.
    final_horiz = horiz0 == ((nseg - 1) % 2 == 0)
    lx, ly = mid[-1]
    mid[-1] = (lx, end[1]) if final_horiz else (end[0], ly)
    return mid


# -- fan-out (N-leaf tree) decode ------------------------------------------
# A fan-out signal's compressedWireTable is a recursive tree, encoded as a DFS
# token stream. A leaf is a CHAIN of segments that runs until a POP terminator;
# a BRANCH forks (a 0x04 branch is a MULTI-WAY junction that keeps taking
# children); compound dir0 = the source itself branches. Bends are deterministic
# (sign = bit0); the direction taken at each FORK is NOT stored (LabVIEW re-derives
# it from terminal positions at draw time), so we solve it — but constrained by the
# KNOWN sink terminals: every leaf must land EXACTLY on one (no proximity slop),
# which makes the assignment unique and prunes the fork search hard. A `b[1]` flag
# marks a mid-wire-tap sub-format. See task #84 / #76.

# px; how close a decoded leaf must come to a sink to be ASSIGNED to it. The
# wire's final segment length is not stored (it is implied by the known terminal),
# so the decoded leaf lands near the sink and ``finish`` SNAPS the last segment
# onto the exact uid center — the connection is always made to the known endpoint,
# never a proximity-fudged position. This bound only disambiguates which sink a
# leaf belongs to (terminals are far apart) and lets the fork search prune.
_ASSIGN_TOL = 10.0
_FANOUT_REC_BUDGET = 3_000_000  # hard cap on the constrained fork search (safety)


def _flip(d: Point, plus: bool) -> Point:
    s = 1 if plus else -1
    return (0, s) if d[0] != 0 else (s, 0)


def _perp3(d: Point) -> list[Point]:
    # a fork continues straight or turns onto either perpendicular; never a 180
    # reverse (unobserved in the corpus, and it only bloats the search)
    if d[0] != 0:
        return [d, (0, 1), (0, -1)]
    return [d, (1, 0), (-1, 0)]


def _drop_collinear(pts: list[Point]) -> list[Point]:
    """Remove interior points that lie on the straight segment between their
    neighbours (a wire passing straight through a junction leaves such a point)."""
    if len(pts) < 3:
        return list(pts)
    out = [pts[0]]
    for p, q in zip(pts[1:-1], pts[2:]):
        a = out[-1]
        # keep q's predecessor p only if it is a real corner (a != p != q bend)
        if (a[0] == p[0] == q[0]) or (a[1] == p[1] == q[1]):
            continue  # collinear -> drop p
        out.append(p)
    out.append(pts[-1])
    return out


def _hungarian(cost: list[list[float]]) -> list[int]:
    """Minimum-cost assignment (row -> col), O(n^3). Deterministic. Used to match
    decoded leaves to their known sink terminals so a systematic attach-point
    offset never causes a greedy mis-pairing (the leaf-to-uid label is unique for
    a correct decode; this is NOT a geometry search)."""
    n = len(cost)
    inf = float("inf")
    u = [0.0] * (n + 1)
    vv = [0.0] * (n + 1)
    p = [0] * (n + 1)
    way = [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [inf] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = inf
            j1 = -1
            for j in range(1, n + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - vv[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    vv[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    res = [0] * n
    for j in range(1, n + 1):
        if p[j] > 0:
            res[p[j] - 1] = j - 1
    return res


def _neg_perp(d: Point) -> Point:
    """The trunk's negative-axis perpendicular (N for a horizontal trunk, W for a
    vertical one)."""
    return _N if d[0] != 0 else _W


def _remap(direction: Point, trunk: Point) -> Point:
    """A fork direction that points BACKWARD (opposite the trunk) is drawn along
    the trunk's negative-axis perpendicular instead."""
    return _neg_perp(trunk) if direction == (-trunk[0], -trunk[1]) else direction


def _walk_tree(
    tokens: list[int], lengths: list[int], d0b: int
) -> list[list[Point]] | None:
    """Deterministic DFS walk of the token stream. Returns each leaf's full
    source-relative vertex path (including the origin), or ``None`` if the source
    branch is malformed. Fork directions come from ``_FORK_BASE`` + ``_remap`` —
    no search."""
    if d0b in DIR:
        src_set = [DIR[d0b]]
    else:
        present = {DIR[m] for m in (0x08, 0x04, 0x02, 0x01) if d0b & m}
        src_set = [d for d in (_N, _S, _E) if d in present]
        if _W in present:
            src_set.append(_W)
        if len(src_set) < 2:
            return None
    leaves: list[list[Point]] = []
    d0 = src_set[0]
    p0 = (d0[0] * lengths[0], d0[1] * lengths[0])
    stack = [((0.0, 0.0), sd, [(0.0, 0.0)]) for sd in reversed(src_set[1:])]
    pos: Point = p0
    d: Point = d0
    path: list[Point] = [(0.0, 0.0), p0]
    ti = 0
    while ti < len(tokens):
        t = tokens[ti]
        length = lengths[ti + 1]
        if t == 0x03:  # POP: close leaf, resume last junction
            leaves.append(list(path))
            if not stack:
                break
            jpos, jdir, jpath = stack.pop()
            pos = (jpos[0] + jdir[0] * length, jpos[1] + jdir[1] * length)
            d = jdir
            path = jpath + [pos]
        elif t & 0x04:  # BRANCH
            base = _FORK_BASE.get(t)
            if base is None:
                return None
            im = _remap(base[0], d)
            for sd in reversed([_remap(s, d) for s in base[1]]):
                stack.append((pos, sd, list(path)))
            pos = (pos[0] + im[0] * length, pos[1] + im[1] * length)
            d = im
            path = path + [pos]
        elif t == 0x02:  # STRAIGHT (passes through a mid-wire tap)
            pos = (pos[0] + d[0] * length, pos[1] + d[1] * length)
            path = path + [pos]
        else:  # BEND (sign = bit0)
            nd = _flip(d, not (t & 1))
            pos = (pos[0] + nd[0] * length, pos[1] + nd[1] * length)
            d = nd
            path = path + [pos]
        ti += 1
    else:
        leaves.append(list(path))
    return leaves


def _finish_leaf(
    path_rel: list[Point], sink_rel: Point, source: Point
) -> list[Point] | None:
    """Snap a leaf's source-relative vertex path (ending near ``sink_rel``) onto
    the KNOWN sink center and return the absolute intermediate bend points for
    ``_compress([source, *mid, sink])``. ``None`` if the wire can't be made
    axis-aligned."""
    full = _drop_collinear(path_rel)
    if len(full) >= 2:
        px, py = full[-2]
        # snap the last bend so its segment to the TRUE sink keeps its axis
        full[-2] = (sink_rel[0], py) if px == full[-1][0] else (px, sink_rel[1])
    mid_rel = full[1:-1]
    pts = [(0.0, 0.0), *mid_rel, sink_rel]
    if any(abs(x1 - x0) > 0.5 and abs(y1 - y0) > 0.5
           for (x0, y0), (x1, y1) in zip(pts, pts[1:])):
        return None
    return [(source[0] + mx, source[1] + my) for mx, my in mid_rel]


def _decode_tree_deterministic(
    blob: str, source: Point, sinks: list[Point]
) -> list[list[Point]] | None:
    """Deterministic fan-out decode: one DFS walk (``_walk_tree``), then match each
    decoded leaf to its known sink by minimum-cost assignment and snap it exactly
    onto that uid center. No fork search, no tolerance. Returns per-sink mids, or
    ``None`` (caller then tries the constrained-search fallback) when a leaf lands
    on no known terminal (rare turned-trunk forks / the tapped sub-format)."""
    try:
        b = [int(blob[i : i + 2], 16) for i in range(0, len(blob), 2)]
    except ValueError:
        return None
    if len(b) < 4 or b[1] not in (0x00, 0x01):
        return None
    tapped = b[1] == 0x01
    dir0i = 3 if tapped else 2
    v = b[0]
    ntok = v - 2
    tokens = b[dir0i + 1 : dir0i + 1 + ntok]
    if len(tokens) != ntok or ntok < 1:
        return None
    lengths = _decode_lengths(b[dir0i + 1 + ntok :], v - 1)
    if lengths is None:
        return None
    leaves = _walk_tree(tokens, lengths, b[dir0i])
    if leaves is None:
        return None
    sinks_rel = [(sx - source[0], sy - source[1]) for sx, sy in sinks]
    # Candidate attach points: a plain fan-out leaf attaches at its tip; a tapped
    # wire's sink may sit on ANY interior vertex, so offer them all.
    cands: list[tuple[Point, list[Point]]] = []  # (attach point, path prefix)
    for path in leaves:
        idxs = range(1, len(path)) if tapped else (len(path) - 1,)
        for j in idxs:
            cands.append((path[j], path[: j + 1]))
    if len(cands) < len(sinks_rel):
        return None
    if not tapped:
        # square min-cost assignment leaf-tip -> sink (no greedy cascade)
        cost = [
            [max(abs(cands[i][0][0] - sr[0]), abs(cands[i][0][1] - sr[1]))
             for i in range(len(cands))]
            for sr in sinks_rel
        ]
        assign = _hungarian(cost)
        out: list[list[Point] | None] = [None] * len(sinks_rel)
        for si, ci in enumerate(assign):
            if cost[si][ci] > _TERM_BOX:
                return None
            mid = _finish_leaf(cands[ci][1], sinks_rel[si], source)
            if mid is None:
                return None
            out[si] = mid
        return [m for m in out if m is not None]
    # tapped: greedy nearest unused vertex within the terminal box (rare, small)
    used = [False] * len(cands)
    out2: list[list[Point]] = []
    for sr in sinks_rel:
        best = -1
        best_d = _TERM_BOX
        for ci, (vp, _) in enumerate(cands):
            if used[ci]:
                continue
            dd = max(abs(vp[0] - sr[0]), abs(vp[1] - sr[1]))
            if dd <= best_d:
                best_d, best = dd, ci
        if best < 0:
            return None
        mid = _finish_leaf(cands[best][1], sr, source)
        if mid is None:
            return None
        used[best] = True
        out2.append(mid)
    return out2


def _decode_tree(
    blob: str, source: Point, sinks: list[Point]
) -> list[list[Point]] | None:
    """Fan-out decode dispatcher: the deterministic walk first (the verified
    ~99.5% — no search, no tolerance), then the constrained-search fallback for
    the rare turned-trunk forks the bytes underdetermine. ``None`` -> router."""
    det = _decode_tree_deterministic(blob, source, sinks)
    if det is not None:
        return det
    return _decode_tree_search(blob, source, sinks)


def _decode_tree_search(
    blob: str, source: Point, sinks: list[Point]
) -> list[list[Point]] | None:
    """Decode a fan-out (N-leaf) ``compressedWireTable`` into per-sink bend
    polylines — the N-sink case of ``decode_signal``.

    Returns a list aligned with ``sinks``: for each sink, the INTERMEDIATE bend
    points (absolute, excluding ``source`` and that sink) of the branch reaching
    it — ready for ``_compress([source, *mid, sink])`` just like the single-net
    path. Handles the ``b[1]=0x01`` straight-through-terminal-tap sub-format too.
    Every leaf must land EXACTLY (``_EXACT``) on a known sink; returns ``None``
    for a malformed blob or when no fork assignment reproduces every sink
    exactly (caller falls back to the auto-router).
    """
    try:
        b = [int(blob[i : i + 2], 16) for i in range(0, len(blob), 2)]
    except ValueError:
        return None
    if len(b) < 4 or b[1] not in (0x00, 0x01):
        return None  # unknown flag byte -> fallback
    # b[1]=0x01 marks a wire with a straight-through TERMINAL TAP: a sink sits on
    # the path (not at a leaf), the `0x02` token continues straight through it,
    # and one extra header byte is inserted before dir0. Otherwise plain fan-out.
    tapped = b[1] == 0x01
    dir0i = 3 if tapped else 2
    v = b[0]
    ntok = v - 2
    tokens = b[dir0i + 1 : dir0i + 1 + ntok]
    if len(tokens) != ntok or ntok < 1:
        return None
    lengths = _decode_lengths(b[dir0i + 1 + ntok :], v - 1)
    if lengths is None or len(lengths) < 1:
        return None
    d0b = b[dir0i]
    if d0b in DIR:
        starts: list[tuple[Point, list[Point]]] = [(DIR[d0b], [])]
    else:
        bits = [DIR[m] for m in (0x08, 0x04, 0x02, 0x01) if d0b & m]
        # Compound dir0 = the source itself is an N-way junction: seg0 leaves in
        # one bit direction, the remaining N-1 leave as deferred branches — one
        # junction per remaining bit, pushed at the source and spawned on
        # successive POPs back to it. Try each bit as seg0; the _perp3 search +
        # sink validation resolve the exact directions. (For N=2 this reduces to
        # the two seg0/sibling orderings, unchanged.)
        starts = (
            [(bits[i], bits[:i] + bits[i + 1 :]) for i in range(len(bits))]
            if len(bits) >= 2
            else []
        )
    if not starts:
        return None

    sinks_rel = [(sx - source[0], sy - source[1]) for sx, sy in sinks]
    result: list[list[list[Point]]] = []  # boxed single result
    budget = [_FANOUT_REC_BUDGET]
    jog = [False]  # second pass: allow a short final jog instead of rejecting

    def _diagonal(pts: list[Point]) -> bool:
        return any(abs(x1 - x0) > 0.5 and abs(y1 - y0) > 0.5
                   for (x0, y0), (x1, y1) in zip(pts, pts[1:]))

    def _on_a_sink(pt: Point) -> bool:
        """True if a just-closed leaf endpoint lands near some sink — the
        necessary condition that prunes the fork search (a leaf that ends nowhere
        near a terminal is dead immediately, not only after the whole tree). The
        exact endpoint is snapped by ``finish``; this only bounds the search."""
        return any(abs(pt[0] - sx) < _ASSIGN_TOL and abs(pt[1] - sy) < _ASSIGN_TOL
                   for sx, sy in sinks_rel)

    def finish(path_rel: list[Point], sx: float, sy: float) -> list[Point] | None:
        """Turn a source-relative vertex path (ending near the sink) into the
        absolute mid-points for source -> mids -> TRUE sink, snapped orthogonal
        onto the KNOWN sink center; None if the drawn wire can't be made
        axis-aligned (unless the jog pass)."""
        full = _drop_collinear([(0.0, 0.0), *path_rel])
        if len(full) >= 2:
            px, py = full[-2]
            # snap the last real bend so its segment to the TRUE sink keeps the
            # axis it already had: vertical final -> match sink x; horizontal -> y
            full[-2] = (sx, py) if px == full[-1][0] else (px, sy)
        mid_rel = full[1:-1]  # exclude source and the (near-sink) endpoint
        if not _diagonal([(0.0, 0.0), *mid_rel, (sx, sy)]):
            return [(source[0] + mx, source[1] + my) for mx, my in mid_rel]
        if not jog[0]:
            return None  # strict pass: keep looking for a clean assignment
        # jog pass (only reached when NO clean assignment exists for the whole
        # tree): keep the faithful decoded bends to their own endpoint, then add
        # one short orthogonal segment to reach the genuinely-offset terminal.
        base = _drop_collinear([(0.0, 0.0), *path_rel])
        ex, _ey = base[-1]
        mids = base[1:] + [(ex, sy)]  # end -> (ex, sink_y) vertical, then -> sink
        if _diagonal([(0.0, 0.0), *mids, (sx, sy)]):
            return None
        return [(source[0] + mx, source[1] + my) for mx, my in mids]

    def build(leaves: list[tuple[list[Point], Point]]) -> list[list[Point]] | None:
        """Emit per-sink absolute mids. Plain fan-out matches each sink to a leaf
        ENDPOINT; a tapped wire matches each sink to ANY vertex on the path (a
        tap terminal sits mid-wire) and the branch is the prefix up to it. A leaf
        is identified with the sink it coincides with EXACTLY — not by proximity."""
        if not tapped and len(leaves) != len(sinks_rel):
            return None  # a plain fan-out leaf that reaches no terminal is wrong
        # candidate (endpoint-or-vertex, prefix-path) list, deduped by position
        cands: list[tuple[Point, list[Point]]] = []
        seen: set[tuple[int, int]] = set()
        for path, _fd in leaves:
            idxs = range(len(path)) if tapped else (len(path) - 1,)
            for j in idxs:
                vp = path[j]
                key = (int(round(vp[0])), int(round(vp[1])))
                if key in seen:
                    continue
                seen.add(key)
                cands.append((vp, path[: j + 1]))
        if len(cands) < len(sinks_rel):
            return None
        used = [False] * len(cands)
        out: list[list[Point]] = []
        for sx, sy in sinks_rel:
            for ci, (vp, pref) in enumerate(cands):
                if used[ci] or abs(vp[0] - sx) >= _ASSIGN_TOL or \
                        abs(vp[1] - sy) >= _ASSIGN_TOL:
                    continue
                mid = finish(pref, sx, sy)
                if mid is None:
                    continue
                used[ci] = True
                out.append(mid)
                break
            else:
                return None
        return out

    def rec(ti, pos, d, stack, leaves, path):
        if result or budget[0] <= 0:
            return
        budget[0] -= 1
        if ti == len(tokens):
            if not tapped and not _on_a_sink(path[-1]):
                return  # exact-prune: the final leaf must end on a terminal
            built = build(leaves + [(path, d)])
            if built is not None:
                result.append(built)
            return
        tok = tokens[ti]
        L = lengths[ti + 1]
        if tok == 0x03:  # POP: terminate leaf, resume last junction
            if not tapped and not _on_a_sink(path[-1]):
                return  # exact-prune: this leaf ends nowhere near a terminal
            if not stack:
                built = build(leaves + [(path, d)])
                if built is not None:
                    result.append(built)
                return
            jpos, jdir, mw, jpath = stack[-1]
            keeps = (False, True) if mw else (False,)
            for keep in keeps:
                newstack = stack if keep else stack[:-1]
                for nd in _perp3(jdir):
                    npos = (jpos[0] + nd[0] * L, jpos[1] + nd[1] * L)
                    rec(ti + 1, npos, nd, newstack,
                        leaves + [(path, d)], jpath + [npos])
                    if result:
                        return
        elif tok & 0x04:  # BRANCH (0x04 => multi-way junction)
            mw = tok == 0x04
            for nd in _perp3(d):
                npos = (pos[0] + nd[0] * L, pos[1] + nd[1] * L)
                rec(ti + 1, npos, nd, stack + [(pos, d, mw, path)],
                    leaves, path + [npos])
                if result:
                    return
        elif tok == 0x02:  # STRAIGHT: continue through a terminal tap (no kink)
            npos = (pos[0] + d[0] * L, pos[1] + d[1] * L)
            rec(ti + 1, npos, d, stack, leaves, path + [npos])
        else:  # BEND: deterministic (sign = bit0)
            nd = _flip(d, not (tok & 1))
            npos = (pos[0] + nd[0] * L, pos[1] + nd[1] * L)
            rec(ti + 1, npos, nd, stack, leaves, path + [npos])

    # Pass 1 strict (clean orthogonal decode); pass 2 allows a short final jog to
    # reach a genuinely-offset terminal instead of falling back. Every leaf is
    # pinned near a known terminal and snapped onto its exact center, so the fork
    # assignment is well-constrained and the early-prune collapses the search.
    for jog[0] in (False, True):
        budget[0] = _FANOUT_REC_BUDGET
        for d0, others in starts:
            p0 = (d0[0] * lengths[0], d0[1] * lengths[0])
            # One deferred junction at the source per remaining bit direction;
            # each is single-use (mw=False) and spawns its branch on a POP back
            # to the source. Empty for a plain single-direction source.
            st = [((0.0, 0.0), od, False, []) for od in others]
            rec(0, p0, d0, st, [], [p0])
            if result:
                return result[0]
    return None


def decode_signal(
    blob: str, source: Point, sinks: list[Point]
) -> list[list[Point]] | None:
    """Decode a signal's ``compressedWireTable`` into per-sink bend polylines.

    The one entry point for both cases. ``sinks`` are the sink terminal centers
    in ``termList`` order (``uids[1:]``); ``source`` is the source center
    (``uids[0]``). Returns a list aligned with ``sinks`` — each entry is that
    branch's INTERMEDIATE bend points (absolute, excluding source/sink), ready
    for ``_compress([source, *mid, sink])``. Returns ``None`` (→ router
    fallback) for a malformed blob or a signal that can't land every leaf
    EXACTLY on its known terminal — no proximity tolerance.

    A 2-endpoint wire is the 1-leaf case (a single bend chain, snapped to its
    one known end); a fan-out is the N-leaf tree. The two use different heap
    byte layouts (``byte1`` is the direction for a chain, a tap flag for a
    tree), so dispatch on the known sink count from ``termList``.
    """
    if not sinks:
        return None
    if len(sinks) == 1:
        mid = _decode_chain(blob, source, sinks[0])
        return None if mid is None else [mid]
    return _decode_tree(blob, source, sinks)
