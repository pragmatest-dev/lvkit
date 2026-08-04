"""Recover a LabVIEW class's private-data field layout from the
``NI.LVClass.FlattenedPrivateDataCTL`` XML property of a source-only
``.lvclass`` file.

A source-only ``.lvclass`` inlines its private-data control's *mutation
history* (every historical version of the control's type, oldest first) as a
LabVIEW packed-string. Decoding that string does **not** yield a flat list of
type descriptors — it yields a complete, self-contained LabVIEW RSRC
container: the private-data ``.ctl``'s own binary file, byte for byte (it
even carries its own ``RSRC``/``LVSR``/``VCTP``/``FPHb`` blocks). That means
the correct way to read it is not to hand-parse packed bytes, but to feed it
straight back into pylabview's own RSRC reader (:mod:`pylabview.LVrsrcontainer`)
exactly as if it were a ``.ctl`` file read from disk.

Generation selection
---------------------
The embedded container's ``VCTP`` block (Consolidated Data Types) holds the
union of every historical version of the private-data cluster the control has
ever had — nothing there is timestamped or version-tagged. The **current**
generation is identified through a different, unambiguous block: ``DTHP``
("Data Types for Heap"). Per pylabview's own documentation for that block,
it stores an ``(indexShift, tdCount)`` slice into ``VCTP``'s "top level" type
list that is *"the slice which is used in Heaps"* — i.e. the type IDs
actually referenced by the control's current front-panel/block-diagram
objects, which by construction is always the most recently saved generation
(older generations are kept in ``VCTP`` only so older callers' saved data
still type-checks against them; nothing heap-side points at them any more).
``DTHP``'s slice is exactly the standin for the modification list a plain
"take the last cluster in the blob" heuristic could not reliably give:
verified against both the BEFORE (2-field) and AFTER (5-field) samples,
``DTHP``'s slice contains only the current generation's TypeDescs (10 of 25
top-level TDs for BEFORE; 35 of 51 for AFTER) and specifically excludes the
older ``"measurement context data"`` clusters that sit earlier in ``VCTP``.

Within that current-generation slice, heap type ID 1 (the very first entry)
is consistently the private-data control's own root type in both samples —
the control's front-panel pane has exactly one object (the private-data
cluster itself), and LabVIEW allocates FP heap-object type IDs starting from
that root object, so it is the first ID recorded. (Both samples additionally
carry a duplicate at heap ID 2 pointing at the identical TypeDesc — plausibly
one heap slot for the pane's control object and one for its terminal/DCO —
which is consistent with, but not required by, this rule.)

That root type is not always the field cluster directly: AFTER's root is a
1-member cluster wrapping a "DataValueRef" (Data Value Reference / DVR)
refnum — the class's private data got switched to store its cluster
by-reference. This is peeled away (:func:`_unwrap_to_top_cluster`) by
dereferencing the DVR to the cluster it wraps, and by peeling any ``TypeDef``
indirection along the way, arriving at the true top-level field cluster in
both generations.

Field extraction
-----------------
Each field of the resolved top-level cluster is one of:

* A named ``TypeDef`` member (a control/typedef, e.g. ``PinMapContext.ctl``)
  — the field's *type* is the TypeDef's own qualified path (``labels``); its
  *name* is the label carried by the TypeDef's wrapped inline type (LabVIEW
  stores the instance/member name there, not on the TypeDef TD itself).
  Recurses into the wrapped type's own members for ``sub_fields`` when it is
  itself a cluster.
* A named ``Cluster`` member (an anonymous/inline cluster) — recurses the
  same way.
* Anything else (numeric, string, boolean, array, refnum, ...) — a leaf
  field; ``lv_type_name`` is the bare pylabview ``TD_FULL_TYPE`` name (the
  same vocabulary :mod:`lvkit.structure` already uses, e.g. ``"NumUInt64"``,
  ``"String"``, ``"Array"``, ``"Refnum"``), and a ``UDClassInst`` refnum
  additionally resolves to its qualified ``.lvclass`` name.

Known gap: one AFTER field (``"reserved session infos"``) is a refnum
sub-kind (raw ``obj_type`` 0x73) that is not in pylabview's ``REFNUM_TYPE``
table — its 2-byte "reftype" code (18) collides with an existing entry
(``Queue``), so pylabview's structured body parser misreads its payload and
recovers neither a client list nor (via its own recovery pass) a label. The
*field's own name* is still recovered here deterministically
(:func:`_recover_label`) by re-scanning that one TD's own length-bounded raw
byte window (``TDObject.raw_data``, sized by the TD's own on-disk length
prefix — correct regardless of what its body parser did) for a trailing
Pascal-string that fills the window exactly, which is the same invariant
pylabview's own (unbounded, and here defeated by the runaway body read)
recovery pass relies on. Its internal structure (whatever data the refnum
actually carries) is not decoded; it is reported as a leaf ``"Refnum"``
field with no ``sub_fields``.
"""

from __future__ import annotations

import html
import io
import re
import warnings
from pathlib import Path
from typing import Any

# Import the patch layer before pylabview, matching lvkit.extractor's own
# import order: it installs a compile-time SyntaxWarning filter (fires at
# first pylabview import) plus read-path robustness patches (e.g. skipping
# the write/round-trip sanity pass that crashes on under-populated LVOOP
# type descriptors -- exactly the situation here, an embedded .ctl with no
# external class context of its own).
from lvkit._pylabview_patches import install_pylabview_patches  # isort: skip

import pylabview.LVdatatype as _lv_datatype  # type: ignore[import-untyped]  # noqa: E402
import pylabview.LVdatatyperef as _lv_datatyperef  # type: ignore[import-untyped]  # noqa: E402
import pylabview.LVrsrcontainer as _lv_rsrc  # type: ignore[import-untyped]  # noqa: E402

install_pylabview_patches()

from lvkit.extractor import _make_read_po  # noqa: E402
from lvkit.structure import LVPrivateDataField  # noqa: E402

TD_FULL_TYPE = _lv_datatype.TD_FULL_TYPE
REFNUM_TYPE = _lv_datatyperef.REFNUM_TYPE

_PROPERTY_RE = re.compile(
    r'<Property Name="NI\.LVClass\.FlattenedPrivateDataCTL"[^>]*>(.*?)</Property>',
    re.S,
)

# The RSRC container signature pylabview's RSRCHeader.checkSanity() accepts:
# fmtver>=3 files use the CRLF form, older ones a NUL-padded form.
_RSRC_MAGICS = (b"RSRC\r\n", b"RSRC\x00\x00")


def decode_flattened_ctl_val(val_text: str) -> bytes:
    """Decode a LabVIEW packed-string XML property value to raw bytes.

    Each character encodes 6 bits (``ord(ch) - 0x21``, alphabet ``'!'``..).
    Proven correct against both the BEFORE/AFTER ``MeasurementContext``
    samples -- decodes to a well-formed embedded RSRC container in both.
    """
    s = html.unescape(val_text)
    out = bytearray()
    bits = 0
    acc = 0
    for ch in s:
        v = ord(ch) - 0x21
        if v < 0 or v > 63:
            continue
        acc = (acc << 6) | v
        bits += 6
        if bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    return bytes(out)


class _NamedBytesIO(io.BytesIO):
    """``io.BytesIO`` with a ``.name`` attribute.

    ``pylabview.LVrsrcontainer.VI.__init__`` reads ``rsrc_fh.name`` (for
    ``src_fname``) -- a plain ``BytesIO`` doesn't have one.
    """

    def __init__(self, data: bytes, name: str) -> None:
        super().__init__(data)
        self.name = name


def _find_embedded_rsrc(raw: bytes) -> bytes | None:
    """Locate the embedded RSRC container inside a decoded flattened-CTL
    blob and return the bytes from its signature onward.

    The blob carries an outer wrapper (a small fixed-shape header plus what
    looks like a content checksum, ~33 bytes in both samples) before the
    inner container's own ``RSRC`` signature. Rather than hardcode that
    offset, search for the signature pylabview's own
    ``RSRCHeader.checkSanity()`` requires -- robust to the wrapper's exact
    length varying by LabVIEW version.
    """
    best: int | None = None
    for magic in _RSRC_MAGICS:
        i = raw.find(magic)
        if i != -1 and (best is None or i < best):
            best = i
    if best is None:
        return None
    return raw[best:]


def _valid_label_length(window: bytes, i: int) -> int:
    """Same check as pylabview's ``TDObject.validLabelLength``: is
    ``window[i]`` a Pascal-string length byte whose string fills ``window``
    exactly (allowing one trailing NUL pad byte)?
    """
    w = window[:-1] if (window and window[-1] == 0) else window
    if i >= len(w):
        return 0
    label_len = w[i]
    if len(w) - i != label_len + 1:
        return 0
    tail = w[i + 1 :]
    if all((bt in (0x0D, 0x0A, 0x09)) or bt >= 32 for bt in tail):
        return label_len
    return 0


def _recover_label(raw_data: bytes | None) -> bytes | None:
    """Recover a TD's own trailing Pascal-string label straight from its
    length-bounded raw byte window (``TDObject.raw_data``, sized by the TD's
    own on-disk length prefix -- correct regardless of whether the type's
    body parser misread its payload). Mirrors pylabview's own
    ``parseRSRCDataFinish`` recovery scan, bounded to this one TD's window
    instead of the (here, defeated) rest of the file.
    """
    if not raw_data:
        return None
    for i in range(len(raw_data)):
        n = _valid_label_length(raw_data, i)
        if n > 0:
            w = raw_data[:-1] if raw_data[-1] == 0 else raw_data
            return w[i + 1 : i + n + 1]
    return None


def _resolve_client(client: Any, section: Any) -> Any:
    """Resolve a TD "client" reference (a cluster/typedef/refnum member) to
    its TDObject -- inline (``index == -1``, already ``.nested``) or by
    index into the flat VCTP TypeDesc list.
    """
    index = client.index
    if index == -1:
        return client.nested
    return section.content[index].nested


def _leaf_type_name(td: Any) -> str:
    """Bare LV type name for a non-cluster/non-typedef TD, in the same
    vocabulary ``lvkit.structure`` already uses (``TD_FULL_TYPE`` member
    names: ``"NumUInt64"``, ``"String"``, ``"Array"``, ``"Refnum"``, ...).
    """
    otype = td.otype
    try:
        return TD_FULL_TYPE(otype).name
    except ValueError:
        # obj_type absent from pylabview's table -- e.g. a refnum sub-kind
        # added in a LabVIEW version newer than pylabview covers (obj_type
        # 0x73 observed in the AFTER sample). The top nibble is the general
        # TD_MAIN_TYPE family and is always well-formed (pylabview itself
        # routes construction on it), so classify from that deterministically
        # rather than guessing a specific type.
        if (otype >> 4) == 0x7:
            return "Refnum"
        # A wholly unrecognized family -- surface it rather than silently
        # inventing a made-up primitive name (mirrors _resolve_type_ids's warn).
        warnings.warn(
            f"unrecognized LabVIEW obj_type 0x{otype:02X}; classifying "
            "private-data field as an opaque leaf",
            stacklevel=2,
        )
        return f"Type_0x{otype:02X}"


def _class_qualified_name(td: Any) -> str | None:
    """For a ``UDClassInst`` (user-defined class instance) refnum, its
    qualified ``.lvclass`` path from the parsed ``items`` list -- the same
    "join the path segments with ':'" convention as a TypeDef's qualified
    name.
    """
    items = getattr(td, "items", None)
    if not items:
        return None
    parts: list[str] = []
    for item in items:
        text = getattr(item, "text", None)
        if not isinstance(text, bytes):
            return None
        parts.append(text.decode("mac_roman", errors="replace"))
    return ":".join(p for p in parts if p)


def _label_text(td: Any, text_encoding: str) -> str:
    label = td.label
    oflags = td.oflags
    if not label and (oflags & 0x40):
        label = _recover_label(getattr(td, "raw_data", None))
    if not label:
        return ""
    return label.decode(text_encoding, errors="replace")


# Real private-data nesting is only a few levels deep; this bound (matching
# _unwrap_to_top_cluster's range(8)) backstops a self-referential cluster in a
# malformed embedded control, mirroring the _visited guard in
# structure._resolve_type_ids.
_MAX_TD_DEPTH = 16


def _field_from_td(
    td: Any, section: Any, text_encoding: str, depth: int = 0
) -> LVPrivateDataField:
    otype = td.otype

    if depth >= _MAX_TD_DEPTH:
        # Cycle / pathological nesting in a malformed control -- stop recursing,
        # name what this TD carries directly.
        return LVPrivateDataField(name=_label_text(td, text_encoding))

    if otype == TD_FULL_TYPE.TypeDef:
        labels = td.labels
        qualified = ":".join(
            seg.decode(text_encoding, errors="replace") for seg in labels
        )
        inner = td.clients[0].nested  # always inline
        sub_fields: list[LVPrivateDataField] = []
        if inner.otype == TD_FULL_TYPE.Cluster:
            sub_fields = [
                _field_from_td(
                    _resolve_client(c, section), section, text_encoding, depth + 1
                )
                for c in inner.clients
            ]
        return LVPrivateDataField(
            name=_label_text(inner, text_encoding),
            lv_type_name=qualified,
            sub_fields=sub_fields,
        )

    if otype == TD_FULL_TYPE.Cluster:
        sub_fields = [
            _field_from_td(
                _resolve_client(c, section), section, text_encoding, depth + 1
            )
            for c in td.clients
        ]
        return LVPrivateDataField(
            name=_label_text(td, text_encoding),
            lv_type_name="Cluster",
            sub_fields=sub_fields,
        )

    if getattr(td, "reftype", None) == int(REFNUM_TYPE.UDClassInst):
        classname = _class_qualified_name(td)
        return LVPrivateDataField(
            name=_label_text(td, text_encoding),
            lv_type_name=classname or "Refnum",
        )

    return LVPrivateDataField(
        name=_label_text(td, text_encoding),
        lv_type_name=_leaf_type_name(td),
    )


def _unwrap_to_top_cluster(td: Any, section: Any) -> Any:
    """Peel ``TypeDef`` indirection and a possible single-field
    Cluster-wrapping-a-``DataValueRef`` indirection (a private-data cluster
    that has been switched to store its fields by reference) down to the
    actual top-level private-data ``Cluster`` TD.
    """
    for _ in range(8):  # bounded: real nesting is 1-3 levels deep
        otype = td.otype
        if otype == TD_FULL_TYPE.TypeDef:
            td = td.clients[0].nested
            continue
        if otype == TD_FULL_TYPE.Cluster:
            clients = td.clients
            if len(clients) == 1:
                member = _resolve_client(clients[0], section)
                if getattr(member, "reftype", None) == int(
                    REFNUM_TYPE.DataValueRef
                ):
                    td = _resolve_client(member.clients[0], section)
                    continue
        break
    return td


def parse_flattened_private_data(raw: bytes) -> list[LVPrivateDataField]:
    """Parse the CURRENT-generation ordered top-level private-data fields
    out of a decoded ``NI.LVClass.FlattenedPrivateDataCTL`` blob.

    See the module docstring for the generation-selection rule (``DTHP``'s
    heap-used slice of ``VCTP``) and field-extraction rules. Returns ``[]``
    if the blob doesn't contain a recognizable embedded RSRC container, or
    that container has no ``VCTP``/``DTHP`` blocks, or its current-generation
    root type isn't (after unwrapping) a cluster.
    """
    inner = _find_embedded_rsrc(raw)
    if inner is None:
        return []

    po = _make_read_po()
    fh = _NamedBytesIO(inner, name="private_data.ctl")
    vi = _lv_rsrc.VI(po, rsrc_fh=fh, text_encoding="mac_roman")

    vctp = vi.get("VCTP")
    dthp = vi.get("DTHP")
    if vctp is None or dthp is None:
        return []

    vctp.parseData()
    section = vctp.sections[vctp.active_section_num]
    dthp.parseData()
    dsection = dthp.sections[dthp.active_section_num]
    if dsection.tdCount < 1:
        return []

    root = dthp.getHeapTD(1)
    if root is None:
        return []

    top = _unwrap_to_top_cluster(root, section)
    if top.otype != TD_FULL_TYPE.Cluster:
        return []

    text_encoding = vi.textEncoding
    return [
        _field_from_td(_resolve_client(c, section), section, text_encoding)
        for c in top.clients
    ]


def private_data_from_lvclass_xml(
    lvclass_path: str | Path,
) -> list[LVPrivateDataField]:
    """Read a source-only ``.lvclass``'s ``NI.LVClass.FlattenedPrivateDataCTL``
    property, decode it, and parse the current-generation private-data
    fields from it.

    Returns ``[]`` if the property is absent (e.g. a binary, non-source-only
    class file).
    """
    path = Path(lvclass_path)
    text = path.read_text(encoding="utf-8", errors="replace")
    match = _PROPERTY_RE.search(text)
    if match is None:
        return []
    raw = decode_flattened_ctl_val(match.group(1))
    return parse_flattened_private_data(raw)
