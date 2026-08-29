"""VILibResolver: the composed resolver class for vilib/openg/drivers VIs."""

from __future__ import annotations

from .loader import _VILibLoaderMixin
from .lookup import _VILibLookupMixin
from .variants import _VILibVariantMixin


class VILibResolver(_VILibLoaderMixin, _VILibLookupMixin, _VILibVariantMixin):
    """Resolve vilib VIs to Python equivalents.

    vilib VIs are standard SubVIs that ship with LabVIEW in the vi.lib folder.
    They are identified by their path
    (e.g., "Utility/sysdir.llb/Get System Directory.vi").

    Loads from two sources:
    1. data/vilib-vis.json - Hand-curated VIs with complete Python implementations
    2. data/vilib/*.json - PDF-extracted VIs with terminal info (fallback)
    """
