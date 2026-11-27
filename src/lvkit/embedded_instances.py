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
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .cache_paths import LibraryRoots
from .extractor import extract_vi_xml
from .parser import parse_vi
from .parser.link_table import (
    InstanceSourceLink,
    LinkTableError,
    LinkTableVersion,
    decode_instance_source,
)
from .parser.models import ParsedDependencyRef, ParsedVI
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


def _instances(directory: Path, instance_files: list[str]) -> list[tuple[str, str]]:
    """``(name, file name)`` of each embedded instance of a caller, in the order
    the caller's ``VINS`` block lists them (``instance_files``: the name list,
    then one file per instance, in ``directory``). Empty when the caller has none
    or the name list does not match its files."""
    if not instance_files:
        return []
    names = _instance_names((directory / instance_files[0]).read_bytes())
    if len(names) != len(instance_files) - 1:
        logger.warning(
            "%s: %d instance names for %d embedded instances",
            directory,
            len(names),
            len(instance_files) - 1,
        )
        return []
    return list(zip(names, instance_files[1:], strict=True))


def read_instance_sources(
    directory: Path, instance_files: list[str], roots: LibraryRoots
) -> dict[str, ParsedDependencyRef]:
    """For each embedded instance of a caller, the VI it is an instance of, keyed
    by the instance's own file name (``<guid>.vi``).

    ``roots`` are the library folders of the run asking, which file the instance
    VIs' extractions.

    ``instance_files`` are the caller's ``VINS`` sidecar files
    (``ParsedVIMetadata.instance_files``: the name list, then one file per
    instance) in ``directory``, the caller's extraction folder. Each returned
    ref's ``path_tokens`` are relative to the CALLER file, like every other
    recorded dependency path. A caller with no embedded instances returns
    ``{}``; an embedded VI that is not an instance of another VI is left out.
    """
    sources: dict[str, ParsedDependencyRef] = {}
    for name, file_name in _instances(directory, instance_files):
        instance_xml = extract_vi_xml(directory / file_name, roots=roots)[2]
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


@dataclass(frozen=True)
class EmbeddedInstance:
    """One embedded instance of a caller, read as the VI it is: its extracted
    files and its parse (``source_override`` is ``<caller>/<instance name>``, the
    place the instance has inside its caller)."""

    name: str
    bd_xml: Path
    fp_xml: Path | None
    main_xml: Path | None
    parsed: ParsedVI


@dataclass(frozen=True)
class CallerInstances:
    """Every embedded instance of one caller, by the instance's name: the VI it
    is an instance of (``sources``, as ``read_instance_sources``), the instance read
    as a VI (``instances``), and the error of each one that could not be read
    (``failures``)."""

    sources: dict[str, ParsedDependencyRef]
    instances: dict[str, EmbeddedInstance]
    failures: dict[str, Exception]


def read_caller_instances(
    directory: Path,
    instance_files: list[str],
    roots: LibraryRoots,
    holder: Path,
) -> CallerInstances:
    """The embedded instances of a caller, each read once.

    ``holder`` is the caller's key: an instance has no file of its own, so its
    place is ``holder / <instance name>``, one level inside its caller, which is
    what its own link table's relative paths start from. ``directory`` and
    ``instance_files`` are as for ``read_instance_sources``."""
    instances, failures = _read_instances(directory, instance_files, roots, holder)
    return CallerInstances(
        read_instance_sources(directory, instance_files, roots), instances, failures
    )


def _read_instances(
    directory: Path,
    instance_files: list[str],
    roots: LibraryRoots,
    holder: Path,
) -> tuple[dict[str, EmbeddedInstance], dict[str, Exception]]:
    instances: dict[str, EmbeddedInstance] = {}
    failures: dict[str, Exception] = {}
    for name, file_name in _instances(directory, instance_files):
        try:
            bd_xml, fp_xml, main_xml = extract_vi_xml(
                directory / file_name, roots=roots
            )
            parsed = parse_vi(
                bd_xml=bd_xml,
                fp_xml=fp_xml,
                main_xml=main_xml,
                source_override=holder / name,
            )
        except Exception as exc:
            failures[name] = exc
            continue
        instances[name] = EmbeddedInstance(name, bd_xml, fp_xml, main_xml, parsed)
    return instances, failures


def saved_instance_defaults(
    instances: Mapping[str, EmbeddedInstance],
) -> dict[str, dict[int, str | None]]:
    """For each embedded instance, the value each of its controls holds when a
    call leaves it unwired, by connector-pane slot and keyed by the instance's
    name.

    An Express VI is saved as an instance whose controls carry the settings of
    its dialog as their saved defaults. A value is the control's saved default as
    text, or ``None`` when the control saves none (it holds its type's default).
    Only controls (inputs) on the instance's connector pane are listed.
    """
    defaults: dict[str, dict[int, str | None]] = {}
    for name, instance in instances.items():
        parsed = instance.parsed
        pane = parsed.connector_pane
        if pane is None:
            continue
        controls = {c.uid: c for c in parsed.front_panel.controls}
        defaults[name] = {
            slot.index: controls[slot.fp_dco_uid].default_value
            for slot in pane.slots
            if slot.fp_dco_uid in controls
            and not controls[slot.fp_dco_uid].is_indicator
        }
    return defaults
