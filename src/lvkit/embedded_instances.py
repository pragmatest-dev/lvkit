"""The VI file a call to a malleable VI actually points at.

LabVIEW saves each call of a malleable VI (``.vim``) as a private copy of the
VI, embedded in the caller's ``VINS`` block and named by a GUID
(``<guid>.vi``, library ``Instance``). The caller's own link table records only
that GUID name, so on its own it cannot say which file the call is to. The
embedded copy is itself a VI file, and its link table records the VI it is an
instance of: the first ``VIVI`` link after its ``IVOV`` (instance-to-owner)
link. That link's path is relative to the instance, which sits one level inside
its caller.
"""

from __future__ import annotations

import logging
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

from .extractor import extract_vi_xml
from .parser.link_table import (
    InstanceSourceLink,
    LinkTableError,
    LinkTableVersion,
    decode_instance_source,
)
from .parser.models import ParsedDependencyRef
from .parser.xml_trees import parsed_xml
from .text_encoding import decode_labview_text

logger = logging.getLogger(__name__)

# The ``VINS`` block's section 0 lists the instances' names: a u32 count, then
# one fixed-size slot per instance holding a length-prefixed name. Sections 1..N
# are the instances themselves, in the same order.
_NAME_SLOT_BYTES = 256


def _instance_names(names_bin: bytes) -> list[str]:
    (count,) = struct.unpack_from(">I", names_bin, 0)
    names: list[str] = []
    for i in range(count):
        slot = names_bin[4 + i * _NAME_SLOT_BYTES : 4 + (i + 1) * _NAME_SLOT_BYTES]
        names.append(decode_labview_text(slot[1 : 1 + slot[0]]))
    return names


def _source_link(instance_xml: Path) -> InstanceSourceLink | None:
    """The VI an instance is an instance of, from its extracted main XML (the
    parsed link table, else the raw sidecar pylabview leaves for a table it
    cannot parse)."""
    root = parsed_xml(instance_xml)
    table = root.find("LIvi/Section")
    if table is None:
        return None
    sidecar = table.get("File")
    if sidecar is not None:
        version = LinkTableVersion.of_vi(root)
        if version is None:
            raise LinkTableError("instance records no saving version")
        return decode_instance_source(
            (instance_xml.parent / sidecar).read_bytes(), version
        )
    links = table.find("LVIN")
    if links is None or links.find("IVOV") is None:
        return None
    source = links.find("VIVI")
    if source is None:
        return None
    path_ref = source.find("LinkSavePathRef")
    tp_val = path_ref.get("TpVal") if path_ref is not None else None
    return InstanceSourceLink(
        name_parts=_strings(source, "LinkSaveQualName"),
        path_tokens=_strings(source, "LinkSavePathRef"),
        path_type=int(tp_val) if tp_val is not None else None,
    )


def _strings(link: ET.Element, tag: str) -> tuple[str, ...]:
    return tuple(s.text or "" for s in link.findall(f"{tag}/String"))


def read_instance_sources(
    directory: Path, instance_files: list[str]
) -> dict[str, ParsedDependencyRef]:
    """For each embedded instance of a caller, the VI it is an instance of, keyed
    by the instance's own file name (``<guid>.vi``).

    ``instance_files`` are the caller's ``VINS`` sidecar files
    (``ParsedVIMetadata.instance_files``: the name list, then one file per
    instance) in ``directory``, the caller's extraction folder. Each returned
    ref's ``path_tokens`` are relative to the CALLER file, like every other
    recorded dependency path. A caller with no embedded instances returns
    ``{}``; an embedded VI that is not an instance of another VI is left out.
    """
    if not instance_files:
        return {}
    names = _instance_names((directory / instance_files[0]).read_bytes())
    if len(names) != len(instance_files) - 1:
        logger.warning(
            "%s: %d instance names for %d embedded instances",
            directory,
            len(names),
            len(instance_files) - 1,
        )
        return {}
    sources: dict[str, ParsedDependencyRef] = {}
    for name, file_name in zip(names, instance_files[1:], strict=True):
        instance_xml = extract_vi_xml(directory / file_name)[2]
        if instance_xml is None:
            continue
        try:
            link = _source_link(instance_xml)
        except LinkTableError as exc:
            logger.warning("%s: instance %s: %s", directory, name, exc)
            continue
        if link is None or not link.name_parts or not link.path_tokens:
            continue
        # The instance's path is <caller>/<instance>, one level below the
        # caller's: one leading empty token (one level up) is consumed.
        tokens = list(link.path_tokens)
        if tokens[0] == "":
            tokens = tokens[1:]
        sources[name] = ParsedDependencyRef(
            name=link.name_parts[-1],
            path_tokens=tokens,
            qualified_name=":".join(link.name_parts),
            path_type=link.path_type,
        )
    return sources
