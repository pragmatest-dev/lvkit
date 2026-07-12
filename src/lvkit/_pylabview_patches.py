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

Call :func:`install_pylabview_patches` once, early (lvkit's extractor does this on
import). It is idempotent.
"""

from __future__ import annotations

from decimal import Decimal

import pylabview.LVblock as _lv_block  # type: ignore[import-untyped]
import pylabview.LVdatatype as _lv_datatype  # type: ignore[import-untyped]
import pylabview.LVmisc as _lv_misc  # type: ignore[import-untyped]

_installed = False


def install_pylabview_patches() -> None:
    """Apply all pylabview patches. Idempotent — safe to call more than once.

    1. **``frexpQuadFloat``** (perf) — an O(exponent) ``Decimal``-halving loop
       that runs its full 16384-iteration safety cap (~1.2s each) on
       out-of-quad-range EXT constants. Replaced with an O(1) log jump for
       extreme magnitudes; the exact original loop is kept for normal
       magnitudes, so their output is unchanged by construction.
    2. **``BDPW.scanForHashSalt``** (perf) — brute-forces the block-diagram
       password salt over 256**3 MD5s (~30s on LVOOP/interface VIs whose salt
       source lives in an external class). The salt is password/round-trip
       metadata we never read, so it is stubbed to a fixed value; the
       ``BDHb``/``FPHb`` block XML is unaffected.
    3. **``TDObjectNumber.checkSanity``** (crash guard) — does ``self.prop1 & ~1``
       with no ``None`` guard, while every other method on the class guards
       ``if self.prop1 is not None``. ``prop1`` stays ``None`` for a numeric type
       descriptor that references an external class (LVOOP) — pylabview parses
       the VI in isolation and never reads it — so ``None & ~1`` raises
       ``TypeError`` and aborts the whole parse. Guard it the same way: treat
       ``None`` as valid for the duration of the check, then restore ``None`` so
       ``exportXML`` still omits ``Prop1`` and the emitted XML is unchanged.
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

    # (3) TDObjectNumber.checkSanity — guard prop1 is None (crash guard).
    _orig_checksanity = _lv_datatype.TDObjectNumber.checkSanity

    def _safe_checksanity(self):  # type: ignore[no-untyped-def]
        if self.prop1 is None:
            self.prop1 = 0
            try:
                return _orig_checksanity(self)
            finally:
                self.prop1 = None
        return _orig_checksanity(self)

    _lv_datatype.TDObjectNumber.checkSanity = _safe_checksanity

    _installed = True
