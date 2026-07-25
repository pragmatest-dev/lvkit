"""In-process monkeypatches for pylabview — all of lvkit's pylabview patches
live HERE, in one place.

pylabview is a bidirectional (``.vi`` <-> XML) tool. Several of its code paths do
work that only matters for *writing* or *validating* VIs — work that is
pathologically slow, or outright crashes, on inputs lvkit only ever READS. Since
lvkit never writes a VI back out, these patches neutralise that work.

Every patch below is **output-identical for rendering and codegen** — verified by
gating on the block-diagram XML (``BDHb``/``FPHb``), which is all render/codegen
consume — or, in the crash-guard case, simply prevents an abort with no change to
the emitted XML.

WRITE WARNING: because these patches neutralise write/round-trip machinery
(the password salt, the integrity-validation pass, type annotations), a patched
pylabview must NOT be used to write ``.vi`` files back out — it would produce
incorrect or corrupt VIs for the affected files. See
:func:`install_pylabview_patches` for the per-patch detail. lvkit is read-only,
so this is safe here. (Several of the crashes these guards prevent would already
stop pylabview writing those VIs anyway.)

Call :func:`install_pylabview_patches` once, early (lvkit's extractor does this on
import). It is idempotent.
"""

from __future__ import annotations

import warnings
from decimal import Decimal

# pylabview's LVheap.py uses invalid escape sequences in plain strings (e.g.
# re.match("^\\(...")), which Python 3.12+ emits as a *compile-time*
# SyntaxWarning — and a future Python turns into a hard SyntaxError. A runtime
# monkeypatch can't touch a compile-time warning; it fires the moment pylabview
# is first compiled, which is the import below (this module is imported ahead of
# pylabview by lvkit.extractor). A ``module=``-scoped filter does NOT catch it —
# a compile-time SyntaxWarning is attributed to the *importer's* frame, not
# "pylabview.LVheap", so only a category filter active during the compile
# matches (verified on Windows: module-scoped passes through, category-only
# suppresses). We scope that category filter to exactly this import with
# catch_warnings, leaving the process-wide filter state untouched. The real fix
# is owning the pylabview source (fork/vendor) and correcting it — task #81.
with warnings.catch_warnings():
    warnings.simplefilter("ignore", SyntaxWarning)
    import pylabview.LVblock as _lv_block  # type: ignore[import-untyped]
    import pylabview.LVdatatype as _lv_datatype  # type: ignore[import-untyped]
    import pylabview.LVmisc as _lv_misc  # type: ignore[import-untyped]
    import pylabview.LVrsrcontainer as _lv_rsrc  # type: ignore[import-untyped]
    import pylabview.LVxml as _lv_xml  # type: ignore[import-untyped]

_installed = False


def install_pylabview_patches() -> None:
    """Apply all pylabview patches. Idempotent — safe to call more than once.

    .. warning::
       **READ-ONLY. These patches make lvkit's parse -> render/transpile path
       robust and fast by neutralising work only the WRITE / round-trip
       (XML -> .vi) direction of pylabview needs. If lvkit ever gains a
       VI-WRITING path, a patched pylabview would emit INCORRECT or CORRUPT VIs
       for the affected files** — a stubbed password salt (#2) breaks the
       block-diagram integrity hash, a skipped validation pass (#3) removes the
       self-check, and dropped type annotations (#4) can't be reconstructed. A
       writer must run against UNPATCHED pylabview (or a dedicated write path).
       (For the VIs that hit #3/#4 this is moot: they crash on parse today, so
       pylabview can't faithfully write them back regardless.) lvkit only reads,
       so this is safe here.

    1. **``frexpQuadFloat``** (perf) — an O(exponent) ``Decimal``-halving loop
       that runs its full 16384-iteration safety cap (~1.2s each) on
       out-of-quad-range EXT constants. Replaced with an O(1) log jump for
       extreme magnitudes; the exact original loop is kept for normal
       magnitudes, so their output is unchanged by construction.
    2. **``BDPW.scanForHashSalt``** (perf) — brute-forces the block-diagram
       password salt over 256**3 MD5s (~30s on LVOOP/interface VIs whose salt
       source lives in an external class). The salt is password/round-trip
       metadata we never read, so it is stubbed to a fixed value; the
       ``BDHb``/``FPHb`` block XML is unaffected. (A re-saved VI would carry a
       wrong salt/hash — write path only.)
    3. **``VI.checkSanity``** (crash guard) — skip the read-time sanity
       VALIDATION pass. ``readRSRC`` discards its result (warnings only), and it
       crashes on under-populated type descriptors from LVOOP VIs parsed without
       their external class context — ``prop1`` / ``flavor`` / ``variobj`` all
       stay ``None`` and the per-type checks compare them (``prop1 & ~1``,
       ``flavor > 127``, ``variobj.expectedRSRCSize()``) with no guard. Write /
       round-trip machinery we don't need; parsed data is unaffected
       (``BDHb``/``FPHb`` byte-identical with it skipped).
    4. **``TypeDescListBase.commentSpecialTypes``** (crash guard) — a cosmetic
       pass that annotates type descriptors with purpose text / comments. It
       assumes probe/hilite tables expose ``getNumRepeats()``; for some LVOOP
       VIs a table is a ``TDObjectCluster`` (no such method) -> ``AttributeError``
       aborts the parse. Contributes NOTHING to the block-diagram XML (verified:
       ``BDHb`` byte-identical with it no-op'd); catch the ``AttributeError``,
       equivalent to the upstream ``hasattr(td, 'getNumRepeats')`` guard.
    """
    global _installed
    if _installed:
        return

    # (1) frexpQuadFloat — O(1) log jump for extreme magnitudes.
    _orig_frexp = _lv_misc.frexpQuadFloat
    _ln2 = Decimal(2).ln()

    def _fast_frexp(d, e_largest=16384):  # type: ignore[no-untyped-def]
        # Delegate to the exact original loop for zero/NaN and any normal
        # magnitude (|d| within ~1e280), so those results are byte-identical.
        if d == 0 or d != d or -280 < abs(d).adjusted() < 280:
            return _orig_frexp(d, e_largest)
        neg = d < 0
        ad = -d if neg else d
        e = int(ad.ln() / _ln2) + 1
        f = ad / (Decimal(2) ** e)
        while f >= 1:
            f /= 2
            e += 1
        while f < Decimal("0.5"):
            f *= 2
            e -= 1
        return (-f, e) if neg else (f, e)

    _lv_misc.frexpQuadFloat = _fast_frexp

    # (2) BDPW.scanForHashSalt — stub the block-diagram password salt search.
    def _skip_salt_scan(  # type: ignore[no-untyped-def]
        self, section_num, presalt_data=b"", postsalt_data=b""
    ):
        section = self.sections[section_num]
        section.salt_td_flat_idx = None
        section.salt = b"\x00" * 12
        section.salt_source = None
        return section.salt

    _lv_block.BDPW.scanForHashSalt = _skip_salt_scan

    # (3) VI.checkSanity — skip the read-time sanity VALIDATION pass. readRSRC
    # discards its result (it only emits warnings), and the pass crashes on
    # under-populated type descriptors from LVOOP VIs parsed without their
    # external class context — prop1 / flavor / variobj all stay None and the
    # per-type checkSanity methods compare them (``prop1 & ~1``, ``flavor > 127``,
    # ``variobj.expectedRSRCSize()``) with no None guard. It is write/round-trip
    # machinery we don't need for read-only extraction, and the parsed data is
    # unaffected (BDHb/FPHb byte-identical with it skipped). Keep ``parseData()``
    # (the parse side-effect) but skip the crashing per-block sanity checks.
    def _skip_checksanity(self):  # type: ignore[no-untyped-def]
        for block in self.blocks.values():
            block.parseData()
        return True

    _lv_rsrc.VI.checkSanity = _skip_checksanity

    # (4) TypeDescListBase.commentSpecialTypes — a cosmetic pass that annotates
    # type descriptors with purpose text / data-fill comments. It assumes the
    # probe/hilite tables are repeated types exposing ``getNumRepeats()``, but
    # for some LVOOP VIs a table resolves to a ``TDObjectCluster`` (no such
    # method) -> AttributeError aborts the whole parse. The pass contributes
    # NOTHING to the block-diagram XML (verified: BDHb is byte-identical with
    # this method no-op'd), so catching the AttributeError — equivalent to the
    # upstream ``hasattr(td, 'getNumRepeats')`` guard — is output-safe.
    _orig_comment = _lv_block.TypeDescListBase.commentSpecialTypes

    def _safe_comment(self, section_num):  # type: ignore[no-untyped-def]
        try:
            return _orig_comment(self, section_num)
        except AttributeError:
            return None

    _lv_block.TypeDescListBase.commentSpecialTypes = _safe_comment

    # (5) LVxml control-char escaping. pylabview escapes control chars 0-31 as
    # ``&#xNN;``, but that token means two DIFFERENT things by context:
    #   * inside CDATA it is LITERAL TEXT — valid, and recoverable (pylabview's
    #     own ``unescape_cdata_control_chars`` and lvkit's constant-value
    #     decoder both turn it back into the exact byte). So the CDATA path is
    #     already lossless and must be LEFT ALONE — we only split ``]]>`` there.
    #   * inside an ATTRIBUTE it is a genuine ENTITY REFERENCE, and XML 1.0
    #     cannot hold 0x00-0x08 / 0x0B / 0x0C / 0x0E-0x1F even as a reference
    #     (pylabview's own comment admits the parse then fails) — so binary
    #     bytes in object-name attributes produce INVALID XML that aborts the
    #     read (the #75 ParseError). Only the attribute path replaces those
    #     unrepresentable chars with U+FFFD.
    # An earlier version stripped BOTH paths, which silently corrupted binary
    # constant VALUES (they live in CDATA ``DefaultData``) — a hex ``U32 0x02``
    # became ``xFDFDFDFD`` — so the strip is now attribute-only (task #59).
    _xml_invalid = [i for i in range(32) if i not in (9, 10, 13)]

    def _strip_xml_invalid(text):  # type: ignore[no-untyped-def]
        for i in _xml_invalid:
            if chr(i) in text:
                text = text.replace(chr(i), "�")
        return text

    _orig_attr = _lv_xml.escape_attribute_control_chars
    _orig_cdata = _lv_xml.escape_cdata_control_chars

    def _safe_attr(text):  # type: ignore[no-untyped-def]
        return _orig_attr(_strip_xml_invalid(text))

    def _safe_cdata(text):  # type: ignore[no-untyped-def]
        # Split any ``]]>`` so string data containing it (e.g. a constant that is
        # itself an XML document) can't close the CDATA section early — the
        # standard CDATA escape. pylabview omits this, producing mismatched-tag
        # XML for such constants.
        #
        # Do NOT U+FFFD-strip control chars in the CDATA path: pylabview's own
        # ``escape_cdata_control_chars`` (``_orig_cdata``) already renders every
        # control byte as recoverable ``&#xNN;`` LITERAL TEXT, which is valid
        # inside CDATA and round-trips (``unescape_cdata_control_chars`` /
        # lvkit's constant-value decoder both recover the exact bytes). The
        # earlier blanket strip ran BEFORE that escaper, so it clobbered binary
        # constant values before they could be encoded — a hex ``U32 0x02``
        # constant serialized as ``����`` and rendered ``xFDFDFDFD`` instead of
        # ``x2`` (task #59). U+FFFD is only needed on the ATTRIBUTE path, where
        # ``&#xNN;`` is a genuine ENTITY REFERENCE and XML 1.0 rejects it for
        # control chars (the real #75 ParseError).
        text = text.replace("]]>", "]]]]><![CDATA[>")
        return _orig_cdata(text)

    _lv_xml.escape_attribute_control_chars = _safe_attr
    _lv_xml.escape_cdata_control_chars = _safe_cdata

    # (6) VCTP.exportXMLTypeDescList — per-type resilient serialization of the
    # Consolidated Type Pool. A single misaligned/corrupt flat type (e.g. an
    # LVVariant whose garbage 4-byte count 1.36e9 trips ``typedesc_list_limit``,
    # or one that expands to a multi-MB junk DataFill) raises mid-loop; the
    # generic Block section handler (LVblock.exportXMLSection) then catches it at
    # the SECTION level and dumps the ENTIRE VCTP to raw ``.bin`` — so the main
    # XML loses every ``FlatTypeID`` TypeDesc AND the ``<TopLevel>`` map.
    # Downstream, ``parse_type_map_rich`` has nothing to resolve, so every
    # cluster/typedef terminal comes back with no fields — a Bundle/Unbundle By
    # Name on a typedef cluster (e.g. ``binning`` = {vert, horz}) can't recover
    # its field names (task: MasterAcquisitionFile_PCO_IOS).
    #
    # The type STRUCTURE parses fine (each flat type is parsed from its own
    # offset — one bad type doesn't cascade); only serializing that one type
    # blows up. So make the per-type export resilient: a type that fails to
    # serialize becomes a position-preserving ``Void`` stub and the loop
    # continues, so all the VALID types + the ``<TopLevel>`` map still reach the
    # XML. The stub MUST occupy the slot — ``FlatTypeID`` is the ordinal
    # position, and TopLevel maps consolidated->flat by that index.
    #
    # Output-safe: a valid type serializes through the byte-for-byte original
    # code path (same ``exportXML``/``exportXMLFinish`` calls, same fname), so a
    # VI with no corrupt type is byte-identical. ONLY a type that today forces
    # the whole section to unusable ``.bin`` changes — it becomes one stub
    # instead of nuking the pool. (Write caveat as above: a stubbed type can't
    # be written back faithfully — read path only.)
    _orig_tdlist = _lv_block.VCTP.exportXMLTypeDescList

    def _resilient_tdlist(  # type: ignore[no-untyped-def]
        self, section_elem, section_num, section, fname_base
    ):
        TD_FULL_TYPE = _lv_datatype.TD_FULL_TYPE
        TDObject = _lv_datatype.TDObject
        stringFromValEnumOrInt = _lv_datatype.stringFromValEnumOrInt
        for i, clientTD in enumerate(section.content):
            nested = clientTD.nested
            desc = nested.full_name if len(nested.full_name) > 0 else "Type Descriptor"
            if len(nested.purpose) > 0:
                desc += "; " + nested.purpose
            section_elem.append(
                _lv_xml.Comment(f" FlatTypeID {nested.index:d}: {desc} ")
            )
            subelem = _lv_xml.SubElement(section_elem, "TypeDesc")
            try:
                subelem.set(
                    "Type",
                    f"{stringFromValEnumOrInt(TD_FULL_TYPE, nested.otype):s}",
                )
                fname = f"{fname_base:s}_td{i:04d}"
                if not self.po.raw_connectors:
                    nested.exportXML(subelem, fname)
                    nested.exportXMLFinish(subelem)
                else:
                    TDObject.exportXML(nested, subelem, fname)
                    TDObject.exportXMLFinish(nested, subelem)
            except Exception:  # noqa: BLE001 — corrupt type -> position-holding stub
                section_elem.remove(subelem)
                stub = _lv_xml.SubElement(section_elem, "TypeDesc")
                stub.set("Type", "Void")
                stub.set("Format", "corrupt-stub")

    _resilient_tdlist.__wrapped__ = _orig_tdlist  # type: ignore[attr-defined]
    _lv_block.VCTP.exportXMLTypeDescList = _resilient_tdlist

    _installed = True
