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
caller already knows).

Only single-net (2-endpoint) signals are decodable this way; a fan-out
signal (3+ endpoints) returns ``None`` and the caller falls back to the
auto-router. This module never runs the algorithm forward from the heap in
any other sense — it is a pure decode of already-validated corpus geometry
(see task #84).
"""

from __future__ import annotations

Point = tuple[float, float]
DIR = {0x08: (1, 0), 0x04: (0, 1), 0x02: (-1, 0), 0x01: (0, -1)}

# When True, wires whose heap `compressedWireTable` decodes are drawn from
# LabVIEW's own routed geometry; undecodable/fan-out wires fall back to the
# auto-router. Default False = byte-identical to the auto-router-only output
# (the A/B baseline). See task #84.
FAITHFUL_WIRE_TABLE = False


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
    if nseg < 1 or b[1] not in DIR or len(b) != 2 + 2 * nbend:
        return None  # fan-out / malformed
    if nseg == 1:
        return []  # straight: scene connects the two centers directly
    lengths = b[2 + nbend : 2 + 2 * nbend]
    signs = b[2 : 2 + nbend]
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
