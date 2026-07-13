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

Only single-net (2-endpoint) signals are decodable this way; a fan-out
signal (3+ endpoints) returns ``None`` and the caller falls back to the
auto-router. This module never runs the algorithm forward from the heap in
any other sense — it is a pure decode of already-validated corpus geometry
(see task #84).
"""

from __future__ import annotations

Point = tuple[float, float]
DIR: dict[int, Point] = {0x08: (1, 0), 0x04: (0, 1), 0x02: (-1, 0), 0x01: (0, -1)}

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


def decode_wire_mid(blob: str, start: Point, end: Point) -> list[Point] | None:
    """Decode the INTERMEDIATE bend points of a 2-endpoint wire's
    ``compressedWireTable`` blob.

    Returns the ``mid`` points only (excluding ``start``/``end``), designed
    to drop straight into ``_compress([start, *mid, end])`` exactly as
    ``router.route(...)`` does. The last bend is snapped so the wrapped path
    stays orthogonal; the endpoints themselves are always the true terminal
    centers. Returns ``None`` for fan-out / malformed blobs.
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


# -- fan-out (3+ endpoint) decode -----------------------------------------
# A fan-out signal's compressedWireTable is a recursive tree, encoded as a DFS
# token stream. A leaf is a CHAIN of segments that runs until a POP terminator;
# a BRANCH forks (a 0x04 branch is a MULTI-WAY junction that keeps taking
# children); compound dir0 = the source itself branches. Bends are deterministic
# (sign = bit0); the direction taken at each FORK is under-determined by the
# bytes (LabVIEW re-derives it from terminal positions), so we resolve it by
# validating leaf endpoints against the known sink centers. A `b[1]` flag marks
# a mid-wire-tap sub-format we don't model -> fall back. See task #84.

_FANOUT_TOL = 10.0          # px; residual is the terminal center-vs-attach offset
_FANOUT_REC_BUDGET = 3_000_000  # hard cap on the validated fork search (safety)


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


def decode_fanout(
    blob: str, source: Point, sinks: list[Point]
) -> list[list[Point]] | None:
    """Decode a fan-out ``compressedWireTable`` into per-sink bend polylines.

    Returns a list aligned with ``sinks``: for each sink, the INTERMEDIATE bend
    points (absolute, excluding ``source`` and that sink) of the branch reaching
    it — ready for ``_compress([source, *mid, sink])`` just like the single-net
    path. Handles the ``b[1]=0x01`` straight-through-terminal-tap sub-format too.
    Returns ``None`` for a malformed blob or when no fork-direction assignment
    reproduces every sink (caller falls back to the auto-router).
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

    def finish(path_rel: list[Point], sx: float, sy: float) -> list[Point] | None:
        """Turn a source-relative vertex path (ending ~at the sink) into the
        absolute mid-points for source -> mids -> TRUE sink, snapped orthogonal;
        None if the drawn wire can't be made axis-aligned (unless jog pass)."""
        full = _drop_collinear([(0.0, 0.0), *path_rel])
        if len(full) >= 2:
            px, py = full[-2]
            # snap the last real bend so its segment to the TRUE sink keeps the
            # axis it already had: vertical final -> match sink x; horizontal -> y
            full[-2] = (sx, py) if px == full[-1][0] else (px, sy)
        mid_rel = full[1:-1]  # exclude source and the (approx-sink) endpoint
        if not _diagonal([(0.0, 0.0), *mid_rel, (sx, sy)]):
            return [(source[0] + mx, source[1] + my) for mx, my in mid_rel]
        if not jog[0]:
            return None  # strict pass: a loose match smuggled in a diagonal
        # jog pass (only reached when NO clean assignment exists for the whole
        # tree): keep the faithful decoded bends to their own endpoint, then add
        # one short orthogonal segment to reach the genuinely-offset terminal.
        base = _drop_collinear([(0.0, 0.0), *path_rel])
        ex, ey = base[-1]
        mids = base[1:] + [(ex, sy)]  # end -> (ex, sink_y) vertical, then -> sink
        if _diagonal([(0.0, 0.0), *mids, (sx, sy)]):
            return None
        return [(source[0] + mx, source[1] + my) for mx, my in mids]

    def build(leaves: list[tuple[list[Point], Point]]) -> list[list[Point]] | None:
        """Emit per-sink absolute mids. Plain fan-out matches each sink to a leaf
        ENDPOINT; a tapped wire matches each sink to ANY vertex on the path (a
        tap terminal sits mid-wire) and the branch is the prefix up to it."""
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
                if used[ci] or abs(vp[0] - sx) >= _FANOUT_TOL or \
                        abs(vp[1] - sy) >= _FANOUT_TOL:
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
            built = build(leaves + [(path, d)])
            if built is not None:
                result.append(built)
            return
        tok = tokens[ti]
        L = lengths[ti + 1]
        if tok == 0x03:  # POP: terminate leaf, resume last junction
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
    # reach a genuinely-offset terminal instead of falling back. Clean-first, so
    # a wire that decodes cleanly never gets a jog.
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
