"""The ``uses :`` entries of a VI that calls malleable VIs (lvnet §7a).

A call of a ``.vim`` runs the copy LabVIEW saved in the calling VI's file, with
the types wired to that call and the frame each Type Specialization Structure
accepted (``graph/instance_loading.py``). The call's ``uses :`` entry shows that
copy's connector pane and accepted frames, so two calls of one ``.vim`` whose
copies differ are two entries; copies with one pane and the same frames are one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lvkit.models import DisableStructureKind

from .core import uid_of
from .models import DisableStructureNode, VINode
from .netlist_models import TypeSpecialization

if TYPE_CHECKING:
    from .core import InMemoryVIGraph


@dataclass
class SavedCopyCalls:
    """What the calls of one VI run: by ``.vim`` file, the keys of the saved copies
    its calls run (``by_file``), by qualified name those of a VI whose file is not
    found (``by_name``), and the files some call runs as the file itself
    (``as_file``)."""

    by_file: dict[str, list[str]] = field(default_factory=dict)
    by_name: dict[str, list[str]] = field(default_factory=dict)
    as_file: set[str] = field(default_factory=set)


def saved_copy_calls(graph: InMemoryVIGraph, vi_name: str) -> SavedCopyCalls:
    """The saved copies the calls of ``vi_name`` run, by the VI they are copies of:
    its file, else (the file is not found) the qualified name its caller records
    (sorted keys, each once)."""
    calls = SavedCopyCalls()
    for node in graph.iter_nodes(vi_name):
        if not isinstance(node, VINode) or node.callee_key is None:
            continue
        name = graph.instance_vim(node.callee_key) if node.runs_saved_instance else None
        if node.runs_saved_instance and node.instance_of_key is not None:
            copies = calls.by_file.setdefault(node.instance_of_key, [])
        elif name is not None:
            copies = calls.by_name.setdefault(name, [])
        else:
            calls.as_file.add(node.callee_key)
            continue
        if node.callee_key not in copies:
            copies.append(node.callee_key)
    for copies in (*calls.by_file.values(), *calls.by_name.values()):
        copies.sort()
    return calls


def copy_type_specializations(
    graph: InMemoryVIGraph, copy_key: str
) -> list[TypeSpecialization]:
    """The accepted frame of each Type Specialization Structure of the saved copy
    ``copy_key``, in document order. A structure whose accepted frame the file
    does not give is left out."""
    return [
        TypeSpecialization(node.active_frame, uid_of(node.id))
        for node in graph.iter_nodes(copy_key)
        if isinstance(node, DisableStructureNode)
        and node.kind is DisableStructureKind.TYPE_SPEC
        and node.active_frame is not None
    ]
