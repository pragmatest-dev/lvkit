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

from decimal import Decimal

import pylabview.LVblock as _lv_block  # type: ignore[import-untyped]
import pylabview.LVmisc as _lv_misc  # type: ignore[import-untyped]
import pylabview.LVrsrcontainer as _lv_rsrc  # type: ignore[import-untyped]

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

    _installed = True
