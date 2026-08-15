"""Reverse-engineering probe for the compressedWireTable fan-out fork rule.

This is a RESEARCH harness, not production code. It decodes every fan-out signal
in a chosen set of VIs with a fully DETERMINISTIC walk (no fork search, no
tolerance) and measures how faithfully the decoded leaves land on the known sink
terminals. It exists to drive the remaining reverse-engineering of the fork
direction encoding (see docs/wire-compression-format.md).

Findings banked here (2026-07-13, branch wire-decode-fork-rule):

* Fork token low-2-bits select an ABSOLUTE direction SUBSET of {N,S,E} (the base
  set); the 4th direction (W=0x02) is the "unused bit" here:
      0x4 -> imm N, sib [S,E] (3-way)   0x5 -> imm S, sib [E]
      0x6 -> imm N, sib [E]             0x7 -> imm N, sib [S]
  Beats a relative reading decisively (relative regresses to 92.4%).
* UNIFIED TURNED-TRUNK RULE: any base direction that points BACKWARD (opposite
  the current trunk) is remapped to the trunk's negative-axis perpendicular
  (neg_perp = N for a horizontal trunk, W for a vertical trunk). This is fully
  endpoint-free and reproduces the deep "comb" fan-ins on turned trunks. It also
  corrects an earlier wrong conclusion (that the 0x7 tap side was endpoint-derived
  and not in the bytes): it IS in the bytes -- it is neg_perp of the trunk.
* The source compound-dir0 tee is the SAME rule seeded at the source.
* Result: 99.48% faithful (<=20px) on the reference set with NO fork search and
  NO tolerance. The residual ~0.5% is dominated by provably-misplaced terminals
  (sRN/rSR, task #96), one deep mixed comb (n=8), and two flag=0x01 tapped-format
  edge cases -- not fork-direction errors.

Run:  uv run python scripts/wire_decode_probe.py
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from lvkit.extractor import extract_vi_xml
from lvkit.parser.layout import _LayoutBuilder
from lvkit.parser.wire_table import DIR, _decode_chain, _decode_lengths, _flip

Point = tuple[float, float]
N, S, E, W = (0, -1), (0, 1), (1, 0), (-1, 0)
# Fork base sets keyed by the token's low 2 bits: (immediate, [deferred siblings]),
# absolute directions over {N,S,E} (W is the unused 4th bit). On a TURNED trunk,
# any base direction that points backward (opposite the trunk) is remapped to the
# trunk's negative-axis perpendicular -- see _remap. Endpoint-free.
FORK_BASE = {0x4: (N, [S, E]), 0x5: (S, [E]), 0x6: (N, [E]), 0x7: (N, [S])}


def _neg_perp(d: Point) -> Point:
    """The trunk's negative-axis perpendicular: N for a horizontal trunk (E/W),
    W for a vertical trunk (N/S)."""
    return N if d[0] != 0 else W


def _remap(direction: Point, trunk: Point) -> Point:
    """A fork direction that points BACKWARD (opposite the trunk) is drawn as the
    trunk's negative-axis perpendicular instead."""
    return _neg_perp(trunk) if direction == (-trunk[0], -trunk[1]) else direction


# The reference VIs the fork rule is validated against (last night's + today's).
REFS = [
    "MasterAcquisitionFile_PCO_IOS.vi",
    "Slice 1D Array (I32)__ogtk.vi",
    "List VI Hierarchy__ogtk.vi",
    "Close Generic Object Refnum (Array VI)__ogtk.vi",
    "Reverse 2D Array (DBL)__ogtk.vi",
    "Number to Proper Engl Text__ogtk.vi",
    "U16 Changed__ogtk.vi",
    "Write Panel to INI__ogtk.vi",
    "XML Loop Stack Recursion.vi",
    "TrackDroppedFrames_FP.vi",
]


def walk_tree(blob: str, sinks_n: int) -> list[tuple[float, float]] | None:
    """Deterministic fan-out walk. Returns the leaf endpoints (source-relative) in
    DFS-close order, or None if the blob is malformed / leaf count mismatches."""
    b = [int(blob[i : i + 2], 16) for i in range(0, len(blob), 2)]
    if len(b) < 4 or b[1] not in (0, 1):
        return None
    d0i = 3 if b[1] == 1 else 2
    v = b[0]
    ntok = v - 2
    tk = b[d0i + 1 : d0i + 1 + ntok]
    if len(tk) != ntok or ntok < 1:
        return None
    lg = _decode_lengths(b[d0i + 1 + ntok :], v - 1)
    if lg is None:
        return None
    d0b = b[d0i]
    if d0b in DIR:
        src_set = [DIR[d0b]]
    else:
        present = {DIR[m] for m in (0x08, 0x04, 0x02, 0x01) if d0b & m}
        src_set = [d for d in (N, S, E) if d in present] + ([W] if W in present else [])
        if len(src_set) < 2:
            return None
    leaves: list[tuple[float, float]] = []
    d0 = src_set[0]
    p0 = (d0[0] * lg[0], d0[1] * lg[0])
    stack = [((0.0, 0.0), sd, [(0.0, 0.0)]) for sd in reversed(src_set[1:])]
    pos, d, path, ti = p0, d0, [(0.0, 0.0), p0], 0
    while ti < len(tk):
        t, L = tk[ti], lg[ti + 1]
        if t == 0x03:  # POP
            leaves.append(path[-1])
            if not stack:
                break
            jp, sd, jpa = stack.pop()
            pos = (jp[0] + sd[0] * L, jp[1] + sd[1] * L)
            d, path = sd, jpa + [pos]
        elif t & 0x04:  # BRANCH
            base = FORK_BASE.get(t)
            if base is None:
                return None
            im = _remap(base[0], d)
            for sd in reversed([_remap(s, d) for s in base[1]]):
                stack.append((pos, sd, list(path)))
            pos = (pos[0] + im[0] * L, pos[1] + im[1] * L)
            d, path = im, path + [pos]
        elif t == 0x02:  # STRAIGHT
            pos = (pos[0] + d[0] * L, pos[1] + d[1] * L)
            path = path + [pos]
        else:  # BEND (sign = bit0)
            nd = _flip(d, not (t & 1))
            pos = (pos[0] + nd[0] * L, pos[1] + nd[1] * L)
            d, path = nd, path + [pos]
        ti += 1
    else:
        leaves.append(path[-1])
    if len(leaves) != sinks_n:
        return None
    return leaves


def _hungarian(cost: list[list[float]]) -> list[int]:
    """Min-cost bijection (row -> col), O(n^3). Deterministic."""
    n = len(cost)
    INF = float("inf")
    u = [0.0] * (n + 1)
    vv = [0.0] * (n + 1)
    p = [0] * (n + 1)
    way = [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = INF
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


def measure(faithful_px: float = 20.0) -> None:
    """Report per-branch faithful rate (leaf within faithful_px of its optimally
    matched known terminal). Assignment is a deterministic bijection, NOT a
    geometry search — it only labels which known uid each decoded leaf reaches."""
    gtot = gfaith = 0
    for name in REFS:
        matches = list(Path(".lvkit/cache/samples").rglob(name))
        if not matches:
            print(f"  MISS {name}")
            continue
        bd, _, _ = extract_vi_xml(matches[0])
        root = ET.parse(bd).getroot()
        rr = root.find("root")
        if rr is not None:
            root = rr
        builder = _LayoutBuilder()
        builder.walk(root, 0.0, 0.0)
        tc = builder.terminal_centers
        tot = faith = 0
        for uids, blob in builder.raw_signals:
            src = tc.get(uids[0])
            sinks = [tc.get(u) for u in uids[1:]]
            if src is None or not sinks or any(s is None for s in sinks):
                continue
            tot += len(sinks)
            if len(sinks) == 1:
                if _decode_chain(blob, src, sinks[0]) is not None:
                    faith += 1
                continue
            leaves = walk_tree(blob, len(sinks))
            if leaves is None:
                continue
            sr = [(s[0] - src[0], s[1] - src[1]) for s in sinks]
            m = len(leaves)
            cost = [
                [
                    max(abs(leaves[i][0] - sr[j][0]), abs(leaves[i][1] - sr[j][1]))
                    for j in range(m)
                ]
                for i in range(m)
            ]
            assign = _hungarian(cost)
            faith += sum(1 for i, j in enumerate(assign) if cost[i][j] <= faithful_px)
        gtot += tot
        gfaith += faith
        print(f"  {100 * faith / max(1, tot):6.2f}%  ({faith}/{tot})  {name}")
    print(
        f"\nOVERALL deterministic faithful (<= {faithful_px:.0f}px): "
        f"{gfaith}/{gtot} = {100 * gfaith / max(1, gtot):.3f}%"
    )


if __name__ == "__main__":
    measure()
