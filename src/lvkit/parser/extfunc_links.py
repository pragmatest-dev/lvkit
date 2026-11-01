"""The library each Call Library Function Node names, from the VI's link records.

The node's setup types a library name (``vendorlib.*``, wildcards unresolved) and
LabVIEW records, when it saves the VI, where that name was found:

* ``NEXF`` ("Node To ExtFunc Link"), in the block-diagram link table (``LIbd``,
  under ``BDHP``): ``LinkSaveQualName`` is the typed name, ``LinkSavePathRef`` the
  recorded path, ``ExtFuncLinkStr`` the function, and ``LinkOffsetList`` the heap
  uid of every Call Library Function Node that calls it. pylabview decodes it
  into the main XML for most VIs; for the rest the link table stays the raw
  ``_LIbd.bin`` sidecar, which ``link_table.decode_ext_func_links`` reads.
* ``DSEF`` ("DS To ExtFunc Link"), in the data space (``VIDS``): the same name,
  path and function, but its offsets are data-space offsets, not node uids. It is
  used only to match a node by its library and function when no ``NEXF`` record
  names the node.

A recorded path is relative to the VI when it starts with empty tokens (one empty
is the VI's folder, each further empty one folder up); a single token is a name
the operating system searches for; any other path (rooted in a LabVIEW folder such
as ``<resource>``, or absolute) is loaded by the name alone.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path

from .extfunc_prototype import ExtFuncCall, LibraryPathKind
from .link_table import ExtFuncLink, LinkTableVersion, decode_ext_func_links

# A relative path's type in a ``PTH0`` path record.
_RELATIVE_PATH = 1

# LabVIEW stores a node's ``<nodeName>`` cut to this many characters; the link
# record holds the whole function name.
NODE_NAME_LIMIT = 31


@dataclass(frozen=True)
class ExtFuncLibraryRecord:
    """One link record: the typed library name, the recorded path, the function."""

    typed_name: str
    path_type: int | None
    path_tokens: tuple[str, ...]
    function: str
    node_uids: tuple[int, ...]


def library_path_kind(
    path_type: int | None, tokens: tuple[str, ...]
) -> LibraryPathKind:
    """How a recorded path locates the library."""
    if tokens and tokens[0] == "" and path_type in (_RELATIVE_PATH, None):
        return LibraryPathKind.RELATIVE_TO_VI
    if len(tokens) <= 1 and path_type in (_RELATIVE_PATH, None, 0):
        return LibraryPathKind.BY_NAME
    return LibraryPathKind.OTHER


def _from_binary(link: ExtFuncLink) -> ExtFuncLibraryRecord:
    return ExtFuncLibraryRecord(
        typed_name=link.name_parts[-1] if link.name_parts else "",
        path_type=link.path_type,
        path_tokens=link.path_tokens,
        function=link.function,
        node_uids=link.node_uids,
    )


def _from_xml(element: ET.Element) -> ExtFuncLibraryRecord:
    names = [s.text or "" for s in element.findall("./LinkSaveQualName/String")]
    path = element.find("LinkSavePathRef")
    tokens = (
        tuple(s.text or "" for s in path.findall("String")) if path is not None else ()
    )
    path_type = path.get("TpVal") if path is not None else None
    return ExtFuncLibraryRecord(
        typed_name=names[-1] if names else "",
        path_type=int(path_type) if path_type is not None else None,
        path_tokens=tokens,
        function=element.findtext("ExtFuncLinkStr") or "",
        node_uids=tuple(
            int(o.text, 0) for o in element.findall("./LinkOffsetList/Offset") if o.text
        ),
    )


def read_node_records(root: ET.Element, main_xml: Path) -> list[ExtFuncLibraryRecord]:
    """The ``NEXF`` records of the VI: from the main XML, else from the raw
    ``_LIbd.bin`` sidecar beside it."""
    elements = root.findall(".//LIbd//BDHP/NEXF")
    if elements:
        return [_from_xml(e) for e in elements]
    sidecar = main_xml.with_name(main_xml.stem + "_LIbd.bin")
    version = LinkTableVersion.of_vi(root)
    if sidecar.exists() and version is not None:
        return [
            _from_binary(link)
            for link in decode_ext_func_links(sidecar.read_bytes(), version)
        ]
    return []


def read_data_space_records(root: ET.Element) -> list[ExtFuncLibraryRecord]:
    """The ``DSEF`` records of the VI (their ``node_uids`` are data-space offsets
    and are not used)."""
    return [_from_xml(e) for e in root.findall(".//VIDS//DSEF")]


def _names_function(recorded: str, node_name: str) -> bool:
    """Whether a record's function is the one a node's (possibly cut) name
    names: the same name, or a full-length node name that is a prefix of it."""
    return recorded == node_name or (
        len(node_name) == NODE_NAME_LIMIT and recorded.startswith(node_name)
    )


def _cut_name_reason(call: ExtFuncCall, candidates: list[str]) -> str | None:
    """Why a function name of exactly 31 characters could not be completed: it may
    be cut, and the records name no one function for it."""
    if len(call.function) != NODE_NAME_LIMIT:
        return None
    if candidates:
        return (
            f"the DLL function name {call.function!r} is cut to 31 characters and "
            f"matches {', '.join(sorted(candidates))}"
        )
    return (
        f"the DLL function name {call.function!r} may be cut to 31 characters and "
        "matches no link record of the VI"
    )


def complete_library(
    call: ExtFuncCall,
    uid: str,
    node_records: list[ExtFuncLibraryRecord],
    data_space_records: list[ExtFuncLibraryRecord],
) -> ExtFuncCall:
    """``call`` with its function, its library name as typed and its recorded
    path, from the record that names the node ``uid``, else the data-space record
    with the node's library and function (the exact name first, then a cut name
    that is a prefix). A node no record describes keeps the library its own path
    ends in; a 31-character name no record settles is marked ``cut_name``."""
    by_uid = [r for r in node_records if uid.isdigit() and int(uid) in r.node_uids]
    if by_uid:
        record = by_uid[0]
    else:
        in_library = [
            r
            for r in data_space_records
            if call.library in (r.typed_name, *r.path_tokens[-1:])
        ]
        exact = [r for r in in_library if r.function == call.function]
        found = exact or [
            r for r in in_library if _names_function(r.function, call.function)
        ]
        same = {
            (r.typed_name, r.path_type, r.path_tokens, r.function) for r in found
        }
        if len(same) != 1:
            return replace(
                call,
                cut_name=_cut_name_reason(call, sorted({r.function for r in found})),
            )
        record = ExtFuncLibraryRecord(*next(iter(same)), ())
    return replace(
        call,
        function=record.function or call.function,
        library=record.typed_name or call.library,
        library_path_tokens=record.path_tokens,
        library_path_kind=library_path_kind(record.path_type, record.path_tokens),
    )
