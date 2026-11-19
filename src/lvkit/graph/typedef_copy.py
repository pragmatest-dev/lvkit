"""How a VI's saved copy of a typedef maps onto the loaded typedef.

LabVIEW updates every VI's copy of a typedef to the loaded one, so a VI saved
against an older typedef reads the loaded typedef's field names. The saved copy
keeps its own fields in its own order; each maps to a loaded field:

1. every saved field name exists in the loaded typedef: each saved field is the
   loaded field of the same name (covers fields appended to the typedef and
   fields reordered);
2. else, the saved copy and the loaded typedef have as many fields: each saved
   field is the loaded field at its position (covers a renamed field: NI forum
   reports say a Bundle/Unbundle By Name relinks to the element at its saved
   position when the typedef changes, forums.ni.com/t5/LabVIEW/Unbundle-by-name-
   nodes-incorrectly-re-link-when-defining-type-is/td-p/116039 and
   forums.ni.com/t5/LabVIEW/Wrong-linking-of-cluster-elements-after-deleting-
   elements-from/td-p/3242009);
3. else no saved field names the loaded field it meant: a gap that names the
   typedef and both field lists.
"""

from __future__ import annotations

import dataclasses

from ..models import ClusterField


def saved_as_loaded(
    saved: list[ClusterField], loaded: list[ClusterField]
) -> list[ClusterField] | None:
    """The loaded typedef's field names at the saved copy's positions (each keeps
    the saved copy's type, the type its wire carries); None when the two do not
    correspond."""
    loaded_names = {field.name for field in loaded}
    if all(field.name in loaded_names for field in saved):
        return list(saved)
    if len(saved) == len(loaded):
        return [
            dataclasses.replace(s, name=n.name, labelled=n.labelled)
            for s, n in zip(saved, loaded, strict=True)
        ]
    return None


def incompatible_copy_message(
    typedef_name: str, saved: list[ClusterField], loaded: list[ClusterField]
) -> str:
    """The gap for a saved copy that maps to no loaded field set."""
    was = ", ".join(f.name for f in saved)
    now = ", ".join(f.name for f in loaded)
    return (
        f"the VI is saved with an older copy of the typedef {typedef_name}: its "
        f"fields were [{was}], the loaded typedef's are [{now}]"
    )
