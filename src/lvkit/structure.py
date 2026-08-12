"""Parse LabVIEW library and class files for structural mapping."""

from __future__ import annotations

import re
import warnings
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Any

from lvkit.extractor import extract_vi_xml
from lvkit.models import _LV_TO_PYTHON_TYPE, ClusterField, LVType


@dataclass
class LVMethod:
    """A method in a LabVIEW class."""
    name: str
    vi_path: str
    scope: str  # "public", "private", "protected", "community"
    is_static: bool = False
    is_accessor: bool = False
    accessor_type: str | None = None  # "getter" or "setter"
    accessor_field: str | None = None  # field name being accessed
    # NI.ClassItem.MustOverride -- a child class MUST provide its own
    # implementation of this dynamic-dispatch method. Default False when the
    # property is absent (the overwhelming common case).
    must_override: bool = False
    # NI.ClassItem.MustCallParent -- an override of this method MUST call its
    # parent implementation. Rare; default False when absent.
    must_call_parent: bool = False


@dataclass
class LVPrivateDataField:
    """A private data field in a LabVIEW class."""
    name: str
    python_type: str = "Any"  # Inferred Python type
    default_value: str | None = None  # Default value expression
    lv_type_name: str = ""  # Raw LV type from VCTP (e.g. "String", "Boolean")
    sub_fields: list[LVPrivateDataField] = field(default_factory=list)


@dataclass
class LVClass:
    """A LabVIEW class."""
    name: str
    path: Path
    parent_class: str | None = None
    is_vilib_parent: bool = False
    private_data_ctl: str | None = None
    methods: list[LVMethod] = field(default_factory=list)
    private_data_fields: list[LVPrivateDataField] = field(default_factory=list)
    # NI.Lib.Version, verbatim dotted-quad string (e.g. "1.0.0.7"). Present on
    # ~100% of classes; None if absent.
    version: str | None = None
    # The FULL ancestor chain, nearest-first (immediate parent -> ... ->
    # root) -- built by recursively following parent_class and resolving each
    # ancestor's own .lvclass file on disk (see _build_ancestor_chain). Best
    # effort: stops (without erroring) the moment an ancestor's file can't be
    # located, so it may be a PREFIX of the true chain for a class whose
    # ancestor tree isn't fully present in this checkout.
    ancestors: list[str] = field(default_factory=list)


@dataclass
class LVLibrary:
    """A LabVIEW library."""
    name: str
    path: Path
    version: str | None = None
    members: list[LVLibraryMember] = field(default_factory=list)


@dataclass
class LVProjectItem:
    """An item in a LabVIEW project."""
    name: str
    item_type: str  # "VI", "LVClass", "Library", "Folder", "Document", etc.
    url: str | None  # Relative path to file (None for folders)
    children: list[LVProjectItem] = field(default_factory=list)


@dataclass
class LVProject:
    """A LabVIEW project."""
    name: str
    path: Path
    lv_version: str | None = None
    items: list[LVProjectItem] = field(default_factory=list)


@dataclass
class LVProjectMember:
    """One loadable member declared in a ``.lvproj``, with the tree context a
    flat ``(name, path)`` list loses.

    ``target`` is the nearest build/execution-target ancestor's name (a
    ``.lvproj``'s top-level ``<Item>``s ARE its targets — ``My Computer`` and,
    on real-time/FPGA systems, RT/FPGA targets); every member sits under one.
    ``is_dependency`` is True when the member lives inside the target's auto-
    collected ``Type="Dependencies"`` group (a pulled-in transitive reference,
    mostly vi.lib), False when it's content the developer explicitly placed in
    the project tree. ``path`` is ``proj_dir / url`` unresolved — existence /
    in-repo classification is the index layer's job.
    """
    member_type: str  # "VI" | "Control" | "LVClass" | "Library"
    name: str
    url: str
    path: Path
    target: str
    is_dependency: bool


@dataclass
class LVLibraryMember:
    """A member (VI, class, or nested library) in a library."""
    name: str
    member_type: str  # "VI", "LVClass", "Library"
    url: str


# Method scope mapping. LabVIEW's four member scopes (NI docs: Public /
# Community / Protected / Private) -- value 4 ("community", a.k.a. "package
# scope" in the UI) was previously missing, so those methods silently
# mislabeled as the SCOPE_MAP.get(..., "public") default. Verified against
# measurement-plugin-labview's Session Reservation.lvclass, which carries real
# MethodScope=4 methods.
SCOPE_MAP = {
    1: "public",
    2: "private",
    3: "protected",
    4: "community",
}

# Accessor pattern detection
# Note: Patterns require either:
#   - Space after keyword (Read/Get/Write/Set X.vi) - case-insensitive
#   - Uppercase letter after keyword (getX.vi) - camelCase
# This avoids false positives like setUp -> "setter for Up"
GETTER_PATTERNS = [
    re.compile(r"^Read\s+(.+)\.vi$", re.IGNORECASE),  # "Read FieldName.vi"
    re.compile(r"^Get\s+(.+)\.vi$", re.IGNORECASE),   # "Get FieldName.vi"
    re.compile(r"^get([A-Z].+)\.vi$"),                # "getFieldName.vi" (camelCase)
]

SETTER_PATTERNS = [
    re.compile(r"^Write\s+(.+)\.vi$", re.IGNORECASE),  # "Write FieldName.vi"
    re.compile(r"^Set\s+(.+)\.vi$", re.IGNORECASE),    # "Set FieldName.vi"
    re.compile(r"^set([A-Z].+)\.vi$"),                 # "setFieldName.vi" (camelCase)
]

# Method names that look like accessors but aren't (e.g., test framework methods)
NON_ACCESSOR_METHODS = {
    "setUp", "tearDown", "setUpClass", "tearDownClass",
    "globalSetUp", "globalTearDown",
}


def _detect_accessor(method_name: str) -> tuple[str | None, str | None]:
    """Detect if a method is a getter or setter and extract the field name.

    Returns:
        Tuple of (accessor_type, field_name) or (None, None) if not an accessor.
    """
    # Check if this is a known non-accessor method (e.g., setUp, tearDown)
    base_name = method_name.replace(".vi", "").replace(".VI", "")
    if base_name in NON_ACCESSOR_METHODS:
        return (None, None)

    for pattern in GETTER_PATTERNS:
        match = pattern.match(method_name)
        if match:
            return ("getter", match.group(1))

    for pattern in SETTER_PATTERNS:
        match = pattern.match(method_name)
        if match:
            return ("setter", match.group(1))

    return (None, None)


def _same_class(label_text: str, classname: str) -> bool:
    """Case-insensitive class-name match for a private-data owner Label vs.
    a target ``<Name>.lvclass``.

    LabVIEW class names are effectively case-insensitive — a type
    reference's casing (e.g. ``DAQmx Module Runtime.lvclass``, as recorded
    on a TypeDesc) can differ from the casing the class FILE was actually
    saved under (e.g. ``Daqmx Module runtime.lvclass`` on disk). Compares
    only the last ``:``-qualified component (a library-owned class's
    reference may be library-prefixed).
    """
    def norm(s: str) -> str:
        return s.rsplit(":", 1)[-1].strip().lower()

    return bool(label_text) and norm(label_text) == norm(classname)


def _is_refnum_field(f: LVPrivateDataField) -> bool:
    """True if a private-data field is itself a reference (a refnum / Data Value
    Reference) rather than inline data — i.e. the class stores its real fields
    behind that reference (by-reference private data)."""
    return (f.lv_type_name or "").strip().lower() == "refnum"


def _fields_from_xml(
    xml_path: Path,
    expected_classname: str | None = None,
    allow_display_label_fallback: bool = False,
) -> list[LVPrivateDataField] | None:
    """Find THIS class's private-data cluster TypeDesc in one main XML
    (canonical ``"class private data"`` label, or — opt-in — its control's
    display-name label).

    A class's private-data cluster is wrapped in a ``TypeDef`` TypeDesc whose
    sibling ``<Label Text="...">`` children name the owning class
    (``"<Class>.lvclass"``) and its control (``"<Class>.ctl"``). Two forms
    occur, matched in two passes:

    * **Pass 1 (canonical, authoritative).** The wrapped cluster's own Label is
      ``"...class private data"``. When ``expected_classname`` is given, the
      TypeDef's FIRST ``<Label>`` (its direct owner) must match it
      (case-insensitively, see ``_same_class``) — so a different class merely
      referenced as a parameter is never returned. ``None`` accepts the first
      canonical match unconditionally (legacy behavior).
    * **Pass 2 (opt-in fallback, ``allow_display_label_fallback``).** The
      cluster is labeled with its control's DISPLAY name (e.g.
      ``"measurement context data"``) rather than ``"class private data"``.
      Identified by the TypeDef's owner Labels — the expected class AND its
      ``"<Class>.ctl"`` control (excluding member sub-controls like
      ``"DAQ Tasks.ctl"``); by-reference data (a lone DataValueRef) is deferred
      to the authoritative ``.ctl``/dep path. This lets a SINGLE uploaded VI
      name its own class fields with no ``.lvclass`` attached (better than raw
      ``[index]``). Off by default; only the single-VI resolver
      (``op_walk._own_class_private_data_fields``) enables it.

    Returns the resolved fields, or ``None`` if this XML carries no matching
    private-data TypeDesc. ``None`` means "not found here, keep scanning other
    XMLs" — distinct from ``[]`` ("no fields") — which
    ``_parse_private_data_fields`` relies on.
    """
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError:
        return None
    root = tree.getroot()

    typedefs = [td for td in root.iter("TypeDesc") if td.get("Type") == "TypeDef"]

    def _owner_labels(typedef: ET.Element) -> list[str]:
        return [lbl.get("Text", "") or "" for lbl in typedef.findall("Label")]

    def _owned_by_expected(typedef: ET.Element) -> bool:
        if expected_classname is None:
            return True
        return any(_same_class(t, expected_classname) for t in _owner_labels(typedef))

    def _stem(name: str) -> str:
        # last ":"-qualified component, minus its extension, normalized
        return name.rsplit(":", 1)[-1].rsplit(".", 1)[0].strip().lower()

    def _is_class_private_ctl(typedef: ET.Element) -> bool:
        # A class's OWN private-data control is conventionally named
        # "<Class>.ctl" -- its stem matches the class's. This distinguishes it
        # from member sub-controls (e.g. "DAQ Tasks.ctl") that are ALSO
        # class-owned .ctl typedefs but are fields, not the private data.
        # Only reached from Pass 2, which is guarded by expected_classname.
        assert expected_classname is not None
        cls_stem = _stem(expected_classname)
        return any(
            t.lower().endswith(".ctl") and _stem(t) == cls_stem
            for t in _owner_labels(typedef)
        )

    def _members(typedef: ET.Element) -> list[LVPrivateDataField] | None:
        nested = typedef.find("TypeDesc[@Nested='True']")
        if nested is None:
            return None
        type_ids = [td.get("TypeID") for td in nested.findall("TypeDesc")]
        return _resolve_type_ids(root, type_ids)

    # Pass 1 (canonical, UNCHANGED): the wrapped cluster's Label says "class
    # private data", owned by the expected class. Authority comes from the
    # FIRST <Label> (the direct owner) — scanning all labels here would let a
    # different class that merely appears deeper in the label list win, so this
    # match stays first-label-only exactly as before.
    for typedef in typedefs:
        nested = typedef.find("TypeDesc[@Nested='True']")
        if nested is None:
            continue
        if "class private data" not in (nested.get("Label", "") or "").lower():
            continue
        if expected_classname is not None:
            owner = typedef.find("Label")
            owner_text = owner.get("Text", "") if owner is not None else ""
            if not _same_class(owner_text, expected_classname):
                continue
        fields = _members(typedef)
        if fields:
            return fields

    # Pass 2 (fallback — better than a raw [index]): the cluster is labeled with
    # its control's DISPLAY name, not "class private data". Accept the TypeDef
    # that IS this class's private-data control — its owner Labels name the
    # expected class AND a ".ctl". Owner-authoritative, so it never grabs a
    # different class's cluster; requires ``expected_classname`` to disambiguate.
    if allow_display_label_fallback and expected_classname is not None:
        for typedef in typedefs:
            if not _owned_by_expected(typedef):
                continue
            if not _is_class_private_ctl(typedef):
                continue
            fields = _members(typedef)
            if not fields:
                continue
            # A lone-refnum cluster is a by-reference wrapper (a single Data
            # Value Reference / refnum standing in for the real fields). Pass 2
            # cannot tell a DVR wrapper from a genuine by-value refnum field, so
            # it defers ANY such cluster to the authoritative .ctl/dep path
            # (which dereferences it) rather than surface the bare wrapper name.
            if all(_is_refnum_field(f) for f in fields):
                continue
            return fields
    return None


def _parse_private_data_fields(lvclass_path: Path) -> list[LVPrivateDataField]:
    """Parse THIS class's own private data fields from a method VI's XML in
    the class directory.

    Any method VI that uses the class object will have the
    "Cluster of class private data" type definition in its VCTP section —
    but a VI can also reference OTHER classes' private data (e.g. a
    generic class taken as a parameter), so every candidate match is
    verified against ``lvclass_path``'s own class name (case-insensitively
    — see ``_fields_from_xml``/``_same_class``) before being accepted.
    Without this check the first "class private data" match found in
    directory-glob order could belong to a different class entirely.

    Field names and LV type names are extracted from the same XML's VCTP.

    For user-defined field types (classes, typedefs), the LV type name here
    is bare (e.g. "Refnum"). Full qualification happens at the dep_graph
    level where ownership context is known.

    Prefers already-extracted ``*.xml`` sidecars sitting next to the
    ``.vi`` files (fast path — common when the whole corpus has been run
    through the pipeline before). Falls back to extracting method VIs
    on-demand (one at a time, memory-flat) when no sidecar XML exists yet —
    e.g. a class pulled fresh via ``scripts/pull_samples.sh`` with nothing
    pre-extracted.
    """
    class_dir = lvclass_path.parent
    expected_classname = f"{lvclass_path.stem}.lvclass"

    for xml_path in sorted(class_dir.glob("*.xml")):
        if "_BDHb" in xml_path.name or "_FPHb" in xml_path.name:
            continue
        fields = _fields_from_xml(xml_path, expected_classname)
        if fields:
            return fields

    for vi_path in sorted(class_dir.glob("*.vi")):
        try:
            _bd_xml, _fp_xml, main_xml = extract_vi_xml(vi_path)
        except RuntimeError:
            continue
        if main_xml is None:
            continue
        fields = _fields_from_xml(main_xml, expected_classname)
        if fields:
            return fields

    return []


def _private_field_lvtype(lv_type_name: str) -> LVType | None:
    """Classify a private-data field's raw LV type name into an ``LVType``.

    Shared by dep_graph class-field population (``graph/loading.py``'s
    ``load_lvclass``) and the render resolver's VI-own-inline-copy fallback
    (``render/nodes.py``'s ``_bundle_by_name_glyph``) so both agree on shape.
    """
    if not lv_type_name:
        return None
    # Leaf component for classification
    leaf = lv_type_name.rsplit(":", 1)[-1]
    if leaf.endswith(".lvclass"):
        return LVType(
            kind="class",
            underlying_type=lv_type_name,
            classname=lv_type_name,
        )
    if leaf.endswith(".ctl"):
        return LVType(
            kind="typedef_ref",
            underlying_type=lv_type_name,
            typedef_name=lv_type_name,
        )
    if leaf == "Cluster":
        return LVType(kind="cluster", underlying_type=lv_type_name)
    if leaf == "Array":
        return LVType(kind="array", underlying_type=lv_type_name)
    return LVType(kind="primitive", underlying_type=lv_type_name)


def private_data_field_to_cluster_field(f: LVPrivateDataField) -> ClusterField:
    """Convert a parsed private-data field (with nested ``sub_fields``) to a
    graph ``ClusterField``, preserving the nesting so nMux/IPES-decompose
    flat-index resolution can flatten it consistently in both callers.
    """
    lv_type = _private_field_lvtype(f.lv_type_name)
    if f.sub_fields and lv_type is not None:
        lv_type = dc_replace(
            lv_type,
            fields=[private_data_field_to_cluster_field(sf) for sf in f.sub_fields],
        )
    return ClusterField(name=f.name, type=lv_type)


def _resolve_type_ids(
    root: ET.Element,
    type_ids: list[str | None],
    type_descs: list[ET.Element] | None = None,
    _visited: frozenset[int] | None = None,
) -> list[LVPrivateDataField]:
    """Resolve TypeID references to field definitions from VCTP.

    Gets field name from Label, LV type name from Type attribute,
    and extracts qualified classname from <Item> elements for class fields.
    Recurses into Cluster fields to capture nested sub-fields (needed for
    nMux flat-index resolution across the full cluster hierarchy).
    """
    fields: list[LVPrivateDataField] = []

    if type_descs is None:
        vctp = root.find(".//VCTP/Section")
        if vctp is None:
            return fields
        type_descs = [elem for elem in vctp if elem.tag == "TypeDesc"]

    if _visited is None:
        _visited = frozenset()

    for tid in type_ids:
        if tid is None:
            continue
        try:
            idx = int(tid)
        except ValueError:
            continue
        if idx >= len(type_descs):
            warnings.warn(
                f"TypeID {idx} is out of bounds (VCTP has {len(type_descs)} entries); "
                "skipping field",
                stacklevel=2,
            )
            continue
        if idx in _visited:
            # Circular TypeID reference in malformed VI — skip to avoid infinite loop
            continue

        type_elem = type_descs[idx]

        # Resolve the actual type element (unwrap TypeDef)
        resolved_elem = type_elem
        lv_type = type_elem.get("Type", "")
        if lv_type == "TypeDef":
            nested = type_elem.find("TypeDesc")
            if nested is not None:
                resolved_elem = nested
                lv_type = nested.get("Type", "")

        # Get label: try the resolved element first, then the outer TypeDef wrapper.
        # Every named cluster field must have a label (the field name). If neither
        # element carries one, this is an anonymous structural TypeDesc (e.g. an
        # inline type used for wiring only) and cannot be mapped to a Python field.
        label = resolved_elem.get("Label", "") or type_elem.get("Label", "")
        if not label:
            continue

        # For class refnums, extract qualified classname from <Item> chain
        ref_type = resolved_elem.get("RefType", "")
        if ref_type == "UDClassInst":
            items = type_elem.findall("Item")
            if not items:
                items = resolved_elem.findall("Item")
            if items:
                lv_type = ":".join(it.get("Text", "") for it in items)

        # Recurse into Cluster sub-fields so _flatten_fields works correctly.
        # Guard against malformed circular references by tracking visited indices.
        sub_fields: list[LVPrivateDataField] = []
        if lv_type == "Cluster":
            child_ids = [c.get("TypeID") for c in resolved_elem if c.tag == "TypeDesc"]
            if child_ids:
                sub_fields = _resolve_type_ids(
                    root, child_ids, type_descs, _visited | {idx}
                )

        python_type = _lv_type_to_python(lv_type)
        fields.append(LVPrivateDataField(
            name=label,
            python_type=python_type,
            lv_type_name=lv_type,
            sub_fields=sub_fields,
        ))

    return fields


def _lv_type_to_python(lv_type: str) -> str:
    """Convert LabVIEW type to Python type hint.

    Uses the canonical type mapping from graph_types.py.
    """
    # Additional types not in the core mapping
    extra_types = {
        "Refnum": "Any",  # VI references, notifiers, etc.
        "Array": "list",
        "Cluster": "dict",
    }

    return _LV_TO_PYTHON_TYPE.get(lv_type, extra_types.get(lv_type, "Any"))


def parse_lvclass(lvclass_path: Path | str) -> LVClass:
    """Parse a .lvclass file to extract class structure.

    Args:
        lvclass_path: Path to the .lvclass file

    Returns:
        LVClass with methods, inheritance, and private data info
    """
    lvclass_path = Path(lvclass_path)
    tree = ET.parse(lvclass_path)
    root = tree.getroot()

    class_name = lvclass_path.stem
    private_data_ctl = None
    methods: list[LVMethod] = []

    # Authoritative parent: decoded from NI.LVClass.ParentClassLinkInfo (see
    # _parent_from_link_info). Absence of the property means this class is a
    # root (no parent) -- confirmed against the full JKI-VI-Tester corpus
    # (32/32 classes: every non-root class carries this property, every root
    # class lacks it).
    link_info = _parent_from_link_info(root)
    parent_class = link_info[0] if link_info is not None else None
    is_vilib_parent = link_info[1] if link_info is not None else False

    # Full ancestor chain, nearest-first -- best-effort on-disk resolution of
    # each ancestor's own .lvclass file (see _build_ancestor_chain). Never
    # decodes NI.LVClass.Geneology (opaque, leaks siblings).
    ancestors = _build_ancestor_chain(lvclass_path, parent_class)

    # NI.Lib.Version -- verbatim dotted-quad string, same convention as
    # parse_lvlib's version property below.
    version = None
    for prop in root.findall("Property"):
        if prop.get("Name") == "NI.Lib.Version":
            version = prop.text

    # Parse items recursively (methods and private data can be in folders)
    _parse_items(root, methods, private_data_ctl)

    # Find private data control
    for item in root.findall(".//Item"):
        if item.get("Type") == "Class Private Data":
            private_data_ctl = item.get("Name")
            break

    # Parse private data fields from _Init.xml
    private_data_fields = _parse_private_data_fields(lvclass_path)

    return LVClass(
        name=class_name,
        path=lvclass_path,
        parent_class=parent_class,
        is_vilib_parent=is_vilib_parent,
        private_data_ctl=private_data_ctl,
        methods=methods,
        private_data_fields=private_data_fields,
        version=version,
        ancestors=ancestors,
    )


def _parse_items(
    parent_elem: ET.Element,
    methods: list[LVMethod],
    private_data_ctl: str | None,
) -> None:
    """Recursively parse Item elements to find methods.

    Args:
        parent_elem: Parent XML element to search
        methods: List to append methods to
        private_data_ctl: Name of private data control (if found)
    """
    for item in parent_elem.findall("Item"):
        item_name = item.get("Name", "")
        item_type = item.get("Type", "")
        item_url = item.get("URL", "")

        if item_type == "Folder":
            # Recurse into folders (private, protected, etc.)
            _parse_items(item, methods, private_data_ctl)
        elif item_type == "VI" and item_name.endswith(".vi"):
            # Get method properties
            scope_prop = item.find("Property[@Name='NI.ClassItem.MethodScope']")
            static_prop = item.find("Property[@Name='NI.ClassItem.IsStaticMethod']")
            must_override_prop = item.find(
                "Property[@Name='NI.ClassItem.MustOverride']"
            )
            must_call_parent_prop = item.find(
                "Property[@Name='NI.ClassItem.MustCallParent']"
            )

            scope_val = 1  # default public
            if scope_prop is not None and scope_prop.text:
                try:
                    scope_val = int(scope_prop.text)
                except ValueError:
                    pass

            is_static = False
            if static_prop is not None and static_prop.text:
                is_static = static_prop.text.lower() == "true"

            must_override = False
            if must_override_prop is not None and must_override_prop.text:
                must_override = must_override_prop.text.lower() == "true"

            must_call_parent = False
            if must_call_parent_prop is not None and must_call_parent_prop.text:
                must_call_parent = must_call_parent_prop.text.lower() == "true"

            # Detect accessor methods
            accessor_type, accessor_field = _detect_accessor(item_name)
            is_accessor = accessor_type is not None

            methods.append(LVMethod(
                name=item_name.replace(".vi", ""),
                vi_path=item_url,
                scope=SCOPE_MAP.get(scope_val, "public"),
                is_static=is_static,
                is_accessor=is_accessor,
                accessor_type=accessor_type,
                accessor_field=accessor_field,
                must_override=must_override,
                must_call_parent=must_call_parent,
            ))


def _lv_base64_decode(text: str) -> bytes:
    """Decode a LabVIEW-flavored base64 string (as used in
    ``NI.LVClass.ParentClassLinkInfo``).

    LabVIEW's variant of base64 uses the same 4-chars-to-3-bytes packing as
    standard base64 but a different alphabet: character codes are taken
    directly as ``ord(c) - 33`` (i.e. the alphabet starts at ``'!'``, code
    point 33) rather than the standard RFC 4648 alphabet. There is no
    padding character; a trailing partial group (fewer than 4 chars) is
    simply dropped, which is fine here since the payload we care about
    (printable path components) sits well before the end of the buffer.
    """
    s = "".join(text.split())
    out = bytearray()
    for i in range(0, len(s) - 3, 4):
        n = 0
        for c in s[i : i + 4]:
            n = (n << 6) | ((ord(c) - 33) & 0x3F)
        out += bytes([(n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF])
    return bytes(out)


_PRINTABLE_RUN_RE = re.compile(rb"[ -~]{4,}")
_LVCLASS_TOKEN_RE = re.compile(r"([^\\/<>]+\.lvclass)$")


def _parent_from_link_info(root: ET.Element) -> tuple[str, bool] | None:
    """Decode the authoritative parent class from
    ``NI.LVClass.ParentClassLinkInfo``.

    Every non-root ``.lvclass`` carries this property; its text is
    LabVIEW-base64 (see ``_lv_base64_decode``) wrapping a binary record
    that contains the parent's ``<Name>.lvclass`` followed by a ``PTH0``
    path marker and the path components leading to that parent file. Two
    shapes are seen in the corpus:

    * **in-repo**: a path RELATIVE to the child class's own directory tree
      (e.g. ``TestRunner\\TestRunner.lvclass``).
    * **vi.lib**: a path containing the literal ``<vilib>`` marker (e.g.
      ``<vilib>\\addons\\_JKI Toolkits\\VI Tester\\TextTestRunner.llb\\
      TextTestRunner.lvclass``).

    Root classes (no parent) carry no ``ParentClassLinkInfo`` property at
    all -- confirmed against all 32 classes in the JKI-VI-Tester corpus
    (every non-root class has the property, every root class lacks it).

    Args:
        root: The parsed ``.lvclass`` XML root element.

    Returns:
        ``(parent_class_name, is_vilib)`` where ``parent_class_name`` has
        no ``.lvclass`` suffix (matching ``LVClass.parent_class``'s
        existing bare-name contract), or ``None`` if the property is
        absent (this class is a root).
    """
    raw: str | None = None
    for prop in root.findall("Property"):
        if prop.get("Name") == "NI.LVClass.ParentClassLinkInfo":
            raw = "".join(prop.itertext())
            break
    if not raw or not raw.strip():
        return None

    decoded = _lv_base64_decode(raw)
    printable = [
        m.group().decode("ascii", "replace")
        for m in _PRINTABLE_RUN_RE.finditer(decoded)
    ]
    is_vilib = any("<vilib>" in run for run in printable)

    for run in printable:
        match = _LVCLASS_TOKEN_RE.search(run)
        token = match.group(1) if match else (run if run.endswith(".lvclass") else None)
        if token:
            return (token[: -len(".lvclass")], is_vilib)

    return None


# Memoized stem -> path index of every ``*.lvclass`` under a given root
# directory (see ``_lvclass_stem_index``) -- keyed by the resolved root
# ``_resolve_ancestor_lvclass`` climbs to, so a full-tree ``rglob`` only ever
# runs ONCE per root, however many ancestor levels ``_build_ancestor_chain``
# resolves through it.
_LVCLASS_INDEX_CACHE: dict[Path, dict[str, Path]] = {}


def _lvclass_stem_index(root: Path) -> dict[str, Path]:
    """Stem -> path index of every ``*.lvclass`` under ``root``, built once
    and memoized per root. Ties (two files sharing a stem) resolve to the
    alphabetically-first path -- matching ``sorted(...)[0]``, the previous
    per-level lookup's own tie-break."""
    cached = _LVCLASS_INDEX_CACHE.get(root)
    if cached is not None:
        return cached
    index: dict[str, Path] = {}
    for p in sorted(root.rglob("*.lvclass")):
        index.setdefault(p.stem, p)
    _LVCLASS_INDEX_CACHE[root] = index
    return index


def _resolve_ancestor_lvclass(
    start_dir: Path, bare_name: str, max_levels: int = 6,
) -> Path | None:
    """Best-effort on-disk resolution of an ancestor class's ``.lvclass`` file.

    An ancestor is rarely a direct sibling of its child (e.g.
    ``_TextTestResult.JUnitXML.lvclass`` lives under ``Ant Plugin/Source/...``
    while its parent ``_TextTestResult.lvclass`` lives under
    ``Classes/_TextTestResult/`` -- a different branch of the same repo tree).
    So this climbs UP from ``start_dir`` (bounded by ``max_levels``, so a
    genuinely-missing ancestor -- common: many corpora don't vendor every
    ancestor's file -- can't runaway-scan the filesystem) to a single ROOT
    directory, then looks ``bare_name`` up in that root's memoized stem index
    (``_lvclass_stem_index``) -- one ``rglob`` per root rather than one per
    level, since each level's search scope is a subset of the next (cross-
    directory, cycle-safe: a symlink cycle under the root can make the
    ``rglob`` itself slow, same as before, but never infinite-loop this
    function). Falls back to a case-insensitive stem match (LabVIEW class
    names are effectively case-insensitive -- see ``_same_class``).
    """
    d = start_dir.resolve()
    for _ in range(max_levels):
        parent = d.parent
        if parent == d:
            break
        d = parent
    index = _lvclass_stem_index(d)

    exact = index.get(bare_name)
    if exact is not None:
        return exact
    bare_lower = bare_name.lower()
    for stem, path in index.items():
        if stem.lower() == bare_lower:
            return path
    return None


def _build_ancestor_chain(
    lvclass_path: Path, parent_class: str | None,
) -> list[str]:
    """The FULL ancestor chain, nearest-first, by recursively following
    ``NI.LVClass.ParentClassLinkInfo`` (via ``_parent_from_link_info``) up
    from ``lvclass_path``.

    Deliberately does NOT decode ``NI.LVClass.Geneology`` (an opaque base64
    type-descriptor that also leaks sibling classes) -- this walks the SAME
    authoritative per-class link a child already resolves its own immediate
    parent from, just repeated up the tree. Each ancestor's ``.lvclass`` file
    is located with ``_resolve_ancestor_lvclass``; when that lookup misses (the
    ancestor's file isn't present in this checkout), the chain stops there --
    tolerated, not an error, since the caller only has ``parent_class``'s bare
    name to go on for that ancestor and cannot keep walking without its file.
    """
    ancestors: list[str] = []
    seen = {lvclass_path.stem}
    current_dir = lvclass_path.parent
    current_parent = parent_class
    while (
        current_parent
        and current_parent != "LabVIEW Object"
        and current_parent not in seen
    ):
        ancestors.append(current_parent)
        seen.add(current_parent)
        parent_file = _resolve_ancestor_lvclass(current_dir, current_parent)
        if parent_file is None:
            break
        try:
            parent_root = ET.parse(parent_file).getroot()
        except ET.ParseError:
            break
        link = _parent_from_link_info(parent_root)
        current_parent = link[0] if link is not None else None
        current_dir = parent_file.parent
    return ancestors


_LVLIB_LOADABLE_TYPES = frozenset({"VI", "LVClass", "Library"})


def _collect_lvlib_members(items: list[ET.Element]) -> list[LVLibraryMember]:
    """Flatten ``<Item>`` elements into loadable ``LVLibraryMember``s.

    A real ``.lvlib`` nests members inside ``Type="Folder"`` containers used
    purely for scope grouping (Public/Private/Protected/custom folders) — a
    member's ``URL`` is already the full relative path from the ``.lvlib``,
    independent of that folder nesting, so folders themselves are never
    emitted as members, only recursed into. Other non-loadable item types
    (``Document``, ``Friended Library``, ``Friends List``) are skipped.
    Depth-first / document order, so the result is deterministic.
    """
    members: list[LVLibraryMember] = []
    for item in items:
        item_type = item.get("Type", "")
        if item_type == "Folder":
            members.extend(_collect_lvlib_members(item.findall("Item")))
            continue
        if item_type not in _LVLIB_LOADABLE_TYPES:
            continue
        members.append(LVLibraryMember(
            name=item.get("Name", ""),
            member_type=item_type,
            url=item.get("URL", ""),
        ))
    return members


def parse_lvlib(lvlib_path: Path | str) -> LVLibrary:
    """Parse a .lvlib file to extract library structure.

    Args:
        lvlib_path: Path to the .lvlib file

    Returns:
        LVLibrary with all member VIs and nested items
    """
    lvlib_path = Path(lvlib_path)
    tree = ET.parse(lvlib_path)
    root = tree.getroot()

    lib_name = lvlib_path.stem
    version = None

    # Get version from properties
    for prop in root.findall("Property"):
        if prop.get("Name") == "NI.Lib.Version":
            version = prop.text

    # Parse items — recursing into Type="Folder" containers so members
    # nested under Public/Private/etc. are recovered too (see
    # _collect_lvlib_members).
    members = _collect_lvlib_members(root.findall("Item"))

    return LVLibrary(
        name=lib_name,
        path=lvlib_path,
        version=version,
        members=members,
    )


def parse_lvproj(lvproj_path: Path | str) -> LVProject:
    """Parse a .lvproj file to extract project structure.

    Args:
        lvproj_path: Path to the .lvproj file

    Returns:
        LVProject with all items (VIs, classes, libraries) included in the project
    """
    lvproj_path = Path(lvproj_path)
    tree = ET.parse(lvproj_path)
    root = tree.getroot()

    proj_name = lvproj_path.stem
    lv_version = root.get("LVVersion")

    def parse_item(item_elem: ET.Element) -> LVProjectItem:
        """Recursively parse an Item element."""
        name = item_elem.get("Name", "")
        item_type = item_elem.get("Type", "")
        url = item_elem.get("URL")

        children = []
        for child in item_elem.findall("Item"):
            children.append(parse_item(child))

        return LVProjectItem(
            name=name,
            item_type=item_type,
            url=url,
            children=children,
        )

    items = []
    for item in root.findall("Item"):
        items.append(parse_item(item))

    return LVProject(
        name=proj_name,
        path=lvproj_path,
        lv_version=lv_version,
        items=items,
    )


def get_project_vis(project: LVProject) -> list[tuple[str, Path]]:
    """Extract all VI paths from a parsed project.

    Args:
        project: Parsed LVProject

    Returns:
        List of (vi_name, absolute_path) tuples for all VIs in the project
    """
    proj_dir = project.path.parent
    vis: list[tuple[str, Path]] = []

    def collect_vis(items: list[LVProjectItem]) -> None:
        for item in items:
            if item.item_type == "VI" and item.url:
                vi_path = proj_dir / item.url
                vis.append((item.name, vi_path))
            # Recurse into children (folders, classes with nested VIs, etc.)
            collect_vis(item.children)

    collect_vis(project.items)
    return vis


def get_project_classes(project: LVProject) -> list[tuple[str, Path]]:
    """Extract all lvclass paths from a parsed project.

    Args:
        project: Parsed LVProject

    Returns:
        List of (class_name, absolute_path) tuples for all classes in the project
    """
    proj_dir = project.path.parent
    classes: list[tuple[str, Path]] = []

    def collect_classes(items: list[LVProjectItem]) -> None:
        for item in items:
            if item.item_type == "LVClass" and item.url:
                class_path = proj_dir / item.url
                classes.append((item.name, class_path))
            collect_classes(item.children)

    collect_classes(project.items)
    return classes


def get_project_libraries(project: LVProject) -> list[tuple[str, Path]]:
    """Extract all lvlib paths from a parsed project.

    Args:
        project: Parsed LVProject

    Returns:
        List of (lib_name, absolute_path) tuples for all libraries in the project
    """
    proj_dir = project.path.parent
    libs: list[tuple[str, Path]] = []

    def collect_libs(items: list[LVProjectItem]) -> None:
        for item in items:
            if item.item_type == "Library" and item.url:
                lib_path = proj_dir / item.url
                libs.append((item.name, lib_path))
            collect_libs(item.children)

    collect_libs(project.items)
    return libs


# A loadable member's file kind, keyed by its URL extension. LabVIEW tags a
# control member ``Type="VI"`` in the .lvproj XML even though it is a ``.ctl``,
# so the extension — not the XML ``Type`` — is the faithful member kind (this is
# why ``get_project_vis`` overcounts VIs vs. the project's real ``.vi`` list).
_LVPROJ_MEMBER_KINDS = {
    "vi": "VI",
    "ctl": "Control",
    "lvclass": "LVClass",
    "lvlib": "Library",
}

# Organizational containers under a target: descend to reach members, but the
# container itself is never a member. (``Build`` — Build Specifications — is
# skipped entirely: it holds build OUTPUTS like EXEs, not source members.)
_LVPROJ_SKIP_AS_MEMBER = frozenset({"My Computer", "Folder", "Dependencies"})


def get_project_members(project: LVProject) -> list[LVProjectMember]:
    """Every loadable member (VI/Control/class/library) a ``.lvproj`` declares,
    carrying its target + dependency context (see :class:`LVProjectMember`).

    Walks the item tree once, tracking the nearest target ancestor and whether
    any ancestor is the ``Dependencies`` group, so each member records ``target``
    and ``is_dependency`` — the semantic split between the project's OWN content
    and auto-collected transitive refs. ``Build`` subtrees are skipped whole;
    ``Folder``/``Dependencies``/target containers are descended into but never
    emitted as members. Member KIND is the URL extension, not the XML ``Type``
    (see ``_LVPROJ_MEMBER_KINDS``), so a ``.ctl`` tagged ``Type="VI"`` is a
    ``Control``, and the ``VI`` count matches the project's real ``.vi`` list.

    Unlike ``get_project_vis``/``get_project_classes``/``get_project_libraries``
    (which flatten every subtree, Dependencies included, and drop the URL), this
    preserves the URL and the tree context the membership fact needs.
    """
    proj_dir = project.path.parent
    members: list[LVProjectMember] = []

    def walk(items: list[LVProjectItem], target: str, in_deps: bool) -> None:
        for item in items:
            if item.item_type == "Build":
                continue
            is_dep = in_deps or item.item_type == "Dependencies"
            if item.url and item.item_type not in _LVPROJ_SKIP_AS_MEMBER:
                ext = item.url.rsplit(".", 1)[-1].lower() if "." in item.url else ""
                kind = _LVPROJ_MEMBER_KINDS.get(ext)
                if kind is not None:
                    members.append(
                        LVProjectMember(
                            member_type=kind,
                            name=item.name,
                            url=item.url,
                            path=proj_dir / item.url,
                            target=target,
                            is_dependency=is_dep,
                        )
                    )
            walk(item.children, target, is_dep)

    # A .lvproj's top-level <Item>s are its targets; members live beneath one.
    for top in project.items:
        walk(top.children, top.name, top.item_type == "Dependencies")

    return members


def _library_entry(lib: LVLibrary, rel_path: str) -> dict[str, Any]:
    """The structure-dict shape for one library (shared by directory scan and
    .lvproj discovery so both project sources project identically)."""
    return {
        "name": lib.name,
        "path": rel_path,
        "version": lib.version,
        "members": [
            {"name": m.name, "type": m.member_type, "url": m.url}
            for m in lib.members
        ],
    }


def _class_entry(cls: LVClass, rel_path: str) -> dict[str, Any]:
    """The structure-dict shape for one class (shared by directory scan and
    .lvproj discovery)."""
    return {
        "name": cls.name,
        "path": rel_path,
        "parent_class": cls.parent_class,
        "private_data": cls.private_data_ctl,
        "methods": [
            {
                "name": m.name,
                "scope": m.scope,
                "is_static": m.is_static,
                "vi_path": m.vi_path,
            }
            for m in cls.methods
        ],
    }


def _rel_str(path: Path, root: Path) -> str:
    """`path` relative to `root` as a string, or the bare name if `path` is not
    under `root` (a .lvproj may reference files above/outside its own dir)."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def discover_project_structure(root_path: Path | str) -> dict[str, Any]:
    """Discover LabVIEW project structure from a directory.

    Scans for .lvlib, .lvclass, and .vi files to build a complete
    picture of the project structure.

    Args:
        root_path: Root directory to scan

    Returns:
        Dict with libraries, classes, and standalone VIs
    """
    root_path = Path(root_path)

    structure: dict[str, list[Any]] = {
        "libraries": [],
        "classes": [],
        "standalone_vis": [],
    }

    # Find all libraries
    for lvlib_path in root_path.rglob("*.lvlib"):
        lib = parse_lvlib(lvlib_path)
        structure["libraries"].append(
            _library_entry(lib, str(lvlib_path.relative_to(root_path)))
        )

    # Find all classes
    for lvclass_path in root_path.rglob("*.lvclass"):
        cls = parse_lvclass(lvclass_path)
        structure["classes"].append(
            _class_entry(cls, str(lvclass_path.relative_to(root_path)))
        )

    # Find standalone VIs (not in class directories)
    class_dirs = {Path(cls["path"]).parent for cls in structure["classes"]}

    for vi_path in root_path.rglob("*.vi"):
        rel_path = vi_path.relative_to(root_path)
        # Check if VI is standalone (not in a class or referenced by library)
        if rel_path.parent not in class_dirs:
            structure["standalone_vis"].append(str(rel_path))

    return structure


def discover_structure_from_lvproj(lvproj_path: Path | str) -> dict[str, Any]:
    """Discover project structure from a .lvproj's EXPLICIT member list.

    Same output shape as ``discover_project_structure`` (so ``--json`` /
    ``--plan`` / the summary render identically), but membership comes from
    what the project file actually declares rather than a directory scan —
    files on disk that the project doesn't include are correctly excluded,
    and referenced members are resolved via their URLs.
    """
    project = parse_lvproj(lvproj_path)
    proj_dir = project.path.parent

    structure: dict[str, list[Any]] = {
        "libraries": [],
        "classes": [],
        "standalone_vis": [],
    }

    # Members referenced via LabVIEW alias URLs (/<vilib>/…, /<userlib>/…)
    # are external dependencies, not project source, and don't resolve to a
    # file on disk. The directory scan can only see on-disk source under the
    # root; mirror that here by skipping members whose file is absent.
    for _name, lib_path in get_project_libraries(project):
        if not lib_path.exists():
            continue
        lib = parse_lvlib(lib_path)
        structure["libraries"].append(_library_entry(lib, _rel_str(lib_path, proj_dir)))

    for _name, class_path in get_project_classes(project):
        if not class_path.exists():
            continue
        cls = parse_lvclass(class_path)
        structure["classes"].append(_class_entry(cls, _rel_str(class_path, proj_dir)))

    # Standalone = project VIs that don't live under a class directory (class
    # method VIs are already accounted for above), mirroring the directory scan.
    class_dirs = {Path(cls["path"]).parent for cls in structure["classes"]}
    for _name, vi_path in get_project_vis(project):
        if not vi_path.exists():
            continue
        rel_path = _rel_str(vi_path, proj_dir)
        if Path(rel_path).parent not in class_dirs:
            structure["standalone_vis"].append(rel_path)

    return structure


def generate_python_structure_plan(structure: dict[str, Any]) -> str:
    """Generate a plan for Python module/package structure.

    Args:
        structure: Project structure from discover_project_structure()

    Returns:
        Human-readable plan for Python structure mapping
    """
    lines = ["# Python Structure Plan", ""]

    # Plan for libraries -> Python modules
    if structure["libraries"]:
        lines.append("## Libraries -> Python Modules")
        for lib in structure["libraries"]:
            module_name = _to_python_identifier(lib["name"])
            lines.append(f"\n### {lib['name']} -> {module_name}.py")
            lines.append(f"Path: {lib['path']}")
            if lib["members"]:
                lines.append("Functions:")
                for member in lib["members"]:
                    if member["type"] == "VI":
                        name = member["name"].replace(".vi", "")
                        func_name = _to_python_identifier(name)
                        lines.append(f"  - {func_name}()")
        lines.append("")

    # Plan for classes -> Python classes
    if structure["classes"]:
        lines.append("## Classes -> Python Classes")
        for cls in structure["classes"]:
            class_name = _to_python_class_name(cls["name"])
            parent = cls.get("parent_class")
            parent_str = f"({_to_python_class_name(parent)})" if parent else ""

            lines.append(f"\n### {cls['name']} -> class {class_name}{parent_str}:")
            lines.append(f"Path: {cls['path']}")

            if cls["private_data"]:
                lines.append(f"Instance data: {cls['private_data']}")

            if cls["methods"]:
                lines.append("Methods:")
                for method in cls["methods"]:
                    decorator = "@staticmethod " if method["is_static"] else ""
                    scope = method["scope"]
                    if scope == "private":
                        visibility = "_"
                    elif scope == "protected":
                        visibility = "__"
                    else:
                        visibility = ""
                    method_name = visibility + _to_python_identifier(method["name"])
                    lines.append(f"  - {decorator}{method_name}()")
        lines.append("")

    return "\n".join(lines)


def _to_python_identifier(name: str) -> str:
    """Convert a LabVIEW name to a valid Python identifier."""
    # Replace spaces and special chars with underscores
    result = name.lower()
    result = result.replace(" ", "_")
    result = result.replace("-", "_")
    result = result.replace(".", "_")
    result = result.replace("(", "")
    result = result.replace(")", "")
    # Remove leading numbers
    while result and result[0].isdigit():
        result = result[1:]
    # Ensure not empty
    if not result:
        result = "item"
    return result


def _to_python_class_name(name: str) -> str:
    """Convert a LabVIEW class name to Python PascalCase."""
    # Remove spaces and special chars, capitalize words
    words = name.replace("-", " ").replace("_", " ").replace(".", " ").split()
    return "".join(word.capitalize() for word in words)
