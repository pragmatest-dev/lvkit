"""VILibResolver: the composed resolver class for vilib/openg/drivers VIs."""

from __future__ import annotations

import copy

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

    def fork(self) -> VILibResolver:
        """A private copy: what it learns (``auto_update_terminals``) is not seen by
        this resolver or by any other. It shares every entry with this resolver
        and copies an entry only when it learns something into it."""
        other = copy.copy(self)
        other._vis = dict(self._vis)
        other._by_name = dict(self._by_name)
        other._types = dict(self._types)
        other._variants = {name: list(v) for name, v in self._variants.items()}
        other._by_poly_selector = dict(self._by_poly_selector)
        other._shares_entries = True
        other._owned_entries = set()
        return other
