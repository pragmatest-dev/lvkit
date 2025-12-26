"""Parse and summarize LabVIEW block diagrams."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .parser import BlockDiagram, Constant, FPTerminal, Node, parse_block_diagram, parse_vi_metadata


# Known LabVIEW system directory types with Python equivalents
# Format: (name, python_windows, python_unix)
SYSTEM_DIR_TYPES = {
    0: ("User Home", "USERPROFILE", "HOME"),
    1: ("User Desktop", "USERPROFILE + '/Desktop'", "HOME + '/Desktop'"),
    2: ("User Documents", "USERPROFILE + '/Documents'", "HOME + '/Documents'"),
    3: ("User Application Data", "APPDATA", "HOME + '/.config'"),
    4: ("User Preferences", "APPDATA", "HOME + '/.config'"),
    5: ("User Temporary", "TEMP", "/tmp"),
    6: ("Public Documents", "PUBLIC + '/Documents'", "/usr/share"),
    7: ("Public Application Data", "PROGRAMDATA", "/usr/local/share"),
    8: ("Public Preferences", "PROGRAMDATA", "/etc"),
    9: ("System Core Libraries", "SYSTEMROOT + '/System32'", "/usr/lib"),
    10: ("System Installed Libraries", "PROGRAMFILES", "/usr/local/lib"),
    11: ("Application Files", "PROGRAMFILES", "/opt"),
    12: ("Boot Volume Root", "SYSTEMDRIVE", "/"),
}

# Comprehensive LabVIEW primitive mappings (primResID -> (name, description, python_equivalent))
PRIMITIVE_MAP = {
    # File I/O primitives
    1419: ("Build Path", "Combines base path with name(s) to create full path", "os.path.join(base, *names)"),
    1420: ("Strip Path", "Separates path into directory and filename", "os.path.split(path) -> (dir, name)"),
    1421: ("Path to String", "Converts path to string", "str(path)"),
    1422: ("String to Path", "Converts string to path", "Path(string)"),
    1423: ("Path Type", "Returns type of path (absolute, relative, etc.)", "path.is_absolute()"),
    1502: ("Open/Create/Replace File", "Opens or creates a file", "open(path, mode)"),
    1503: ("Read from Text File", "Reads text from file", "file.read()"),
    1504: ("Write to Text File", "Writes text to file", "file.write(text)"),
    1505: ("Close File", "Closes an open file", "file.close()"),
    1538: ("Read from Binary File", "Reads binary data from file", "file.read() in 'rb' mode"),
    1539: ("Write to Binary File", "Writes binary data to file", "file.write(data) in 'wb' mode"),

    # String primitives
    1051: ("Concatenate Strings", "Joins multiple strings", "''.join(strings) or str1 + str2"),
    1052: ("String Length", "Returns length of string", "len(string)"),
    1053: ("String Subset", "Extracts substring", "string[start:start+length]"),
    1054: ("Search and Replace", "Finds and replaces text", "string.replace(old, new)"),
    1055: ("Match Pattern", "Regex pattern matching", "re.search(pattern, string)"),
    1056: ("Format Into String", "Formats values into string", "format_string % values or f-string"),
    1057: ("Scan From String", "Parses values from string", "parse or regex extract"),

    # Numeric primitives
    1001: ("Add", "Adds two numbers", "a + b"),
    1002: ("Subtract", "Subtracts two numbers", "a - b"),
    1003: ("Multiply", "Multiplies two numbers", "a * b"),
    1004: ("Divide", "Divides two numbers", "a / b"),
    1005: ("Quotient & Remainder", "Integer division with remainder", "divmod(a, b)"),
    1006: ("Increment", "Adds 1 to number", "n + 1"),
    1007: ("Decrement", "Subtracts 1 from number", "n - 1"),
    1008: ("Absolute Value", "Returns absolute value", "abs(n)"),
    1009: ("Round", "Rounds to nearest integer", "round(n)"),
    1010: ("Square Root", "Returns square root", "math.sqrt(n)"),

    # Comparison primitives
    1101: ("Equal?", "Tests equality", "a == b"),
    1102: ("Not Equal?", "Tests inequality", "a != b"),
    1103: ("Greater?", "Tests greater than", "a > b"),
    1104: ("Less?", "Tests less than", "a < b"),
    1105: ("Greater Or Equal?", "Tests greater or equal", "a >= b"),
    1106: ("Less Or Equal?", "Tests less or equal", "a <= b"),
    1107: ("Max & Min", "Returns max and min of inputs", "max(a, b), min(a, b)"),
    1108: ("In Range?", "Tests if value is in range", "low <= x <= high"),

    # Boolean primitives
    1201: ("And", "Logical AND", "a and b"),
    1202: ("Or", "Logical OR", "a or b"),
    1203: ("Not", "Logical NOT", "not a"),
    1204: ("Exclusive Or", "Logical XOR", "a ^ b"),
    1205: ("Implies", "Logical implication", "not a or b"),

    # Array primitives
    1301: ("Array Size", "Returns array dimensions", "len(array) or array.shape"),
    1302: ("Index Array", "Gets element at index", "array[index]"),
    1303: ("Replace Array Subset", "Replaces elements", "array[start:end] = new_values"),
    1304: ("Insert Into Array", "Inserts elements", "array.insert(index, value)"),
    1305: ("Delete From Array", "Removes elements", "del array[index] or array.pop()"),
    1306: ("Initialize Array", "Creates array with initial values", "[value] * size"),
    1307: ("Build Array", "Combines elements/arrays", "list(elements) or np.concatenate"),
    1308: ("Array Subset", "Extracts portion of array", "array[start:start+length]"),
    1309: ("Reshape Array", "Changes array dimensions", "np.reshape(array, shape)"),
    1310: ("Search 1D Array", "Finds element in array", "array.index(value)"),
    1311: ("Sort 1D Array", "Sorts array", "sorted(array)"),
    1312: ("Reverse 1D Array", "Reverses array", "array[::-1]"),

    # Cluster primitives
    1401: ("Bundle", "Creates cluster from elements", "dataclass or namedtuple"),
    1402: ("Unbundle", "Extracts all elements from cluster", "tuple unpacking"),
    1403: ("Bundle By Name", "Creates/modifies cluster by name", "dataclass(**kwargs)"),
    1404: ("Unbundle By Name", "Extracts specific elements", "cluster.field_name"),

    # Timing primitives
    1601: ("Wait (ms)", "Delays execution", "time.sleep(ms / 1000)"),
    1602: ("Tick Count (ms)", "Returns millisecond counter", "time.time() * 1000"),
    1603: ("Get Date/Time", "Returns current date/time", "datetime.datetime.now()"),

    # Dialog primitives
    1701: ("One Button Dialog", "Shows message box", "messagebox.showinfo()"),
    1702: ("Two Button Dialog", "Shows yes/no dialog", "messagebox.askyesno()"),
    1703: ("Three Button Dialog", "Shows dialog with 3 options", "custom dialog"),
}


def decode_labview_path(hex_value: str) -> str:
    """Decode a LabVIEW path constant from hex."""
    try:
        data = bytes.fromhex(hex_value)
        if not data.startswith(b'PTH0'):
            return hex_value  # Not a path, return as-is

        # Skip header and metadata, parse length-prefixed strings
        idx = 12  # Skip PTH0 + length + flags
        parts = []
        while idx < len(data):
            str_len = data[idx]
            idx += 1
            if str_len > 0 and idx + str_len <= len(data):
                parts.append(data[idx:idx + str_len].decode('ascii', errors='replace'))
                idx += str_len
            else:
                break
        return '/'.join(parts)
    except Exception:
        return hex_value


def decode_constant(const: Constant, context_hint: str | None = None) -> tuple[str, str]:
    """Decode a constant value to (type, human-readable value).

    Args:
        const: The constant to decode
        context_hint: Optional hint about what this constant connects to

    Returns:
        Tuple of (type_name, human_readable_value)
    """
    value = const.value

    # Check for path constant
    if value.startswith('50544830'):  # 'PTH0' in hex
        return ("path", decode_labview_path(value))

    # Check for small integer (enum/numeric)
    if len(value) == 8:
        try:
            int_val = int(value, 16)
            # Check if it's a system directory type based on context or label
            label_lower = (const.label or "").lower()
            context_lower = (context_hint or "").lower()
            if ("system directory" in label_lower or
                "system directory" in context_lower or
                "get system directory" in context_lower):
                dir_name = SYSTEM_DIR_TYPES.get(int_val, "unknown")
                return ("system_dir_type", f"{dir_name} ({int_val})")
            return ("int", str(int_val))
        except ValueError:
            pass

    return ("raw", value)


def summarize_vi(bd_xml_path: Path | str, main_xml_path: Path | str | None = None) -> str:
    """Generate a summary of a VI for LLM processing.

    Args:
        bd_xml_path: Path to the block diagram XML (*_BDHb.xml)
        main_xml_path: Path to the main VI XML (optional, for metadata)

    Returns:
        Human-readable summary string
    """
    bd_xml_path = Path(bd_xml_path)
    bd = parse_block_diagram(bd_xml_path)

    # Parse raw XML for additional context (enum labels, etc.)
    tree = ET.parse(bd_xml_path)
    root = tree.getroot()

    # Extract enum labels from the XML
    enum_labels = _extract_enum_labels(root)

    # Get metadata if available
    vi_name = "Unknown VI"
    subvi_refs = []
    if main_xml_path:
        meta = parse_vi_metadata(main_xml_path)
        vi_name = meta.get("name", vi_name)
        subvi_refs = meta.get("subvi_refs", [])
    else:
        # Try to derive name from filename
        vi_name = bd_xml_path.stem.replace("_BDHb", "")

    lines = [f'LabVIEW VI: "{vi_name}"', ""]

    # Build UID-to-node map for semantic wire descriptions
    uid_to_node = {}
    uid_to_const = {}

    for node in bd.nodes:
        uid_to_node[node.uid] = node
    for const in bd.constants:
        uid_to_const[const.uid] = const

    # Describe nodes with Python equivalents
    lines.append("OPERATIONS:")
    node_refs = {}  # Map UID to readable reference
    for i, node in enumerate(bd.nodes, 1):
        ref = f"[{i}]"
        node_refs[node.uid] = ref

        if node.node_type == "iUse":
            lines.append(f'  {ref} SubVI: "{node.name}"')
        elif node.node_type == "prim":
            prim_info = PRIMITIVE_MAP.get(node.prim_res_id)
            if prim_info:
                name, desc, python_eq = prim_info
                lines.append(f"  {ref} {name}: {desc}")
                lines.append(f"       Python: {python_eq}")
            else:
                lines.append(f"  {ref} Unknown Primitive (primResID={node.prim_res_id})")
        elif node.node_type == "whileLoop":
            lines.append(f"  {ref} While Loop: while condition:")
        elif node.node_type == "forLoop":
            lines.append(f"  {ref} For Loop: for i in range(n):")
        elif node.node_type == "select":
            lines.append(f"  {ref} Case/Select: if/elif/else structure")
        elif node.node_type == "propNode":
            lines.append(f'  {ref} Property Node: "{node.name}"')
        elif node.node_type in ("seq", "caseStruct", "eventStruct"):
            lines.append(f"  {ref} {node.node_type}")

    # Describe constants with context
    if bd.constants:
        lines.append("")
        lines.append("CONSTANTS:")
        for const in bd.constants:
            const_ref = f"const_{const.uid}"
            node_refs[const.uid] = const_ref

            # Check if this is an enum value with known labels
            enum_label = _get_enum_value_label(const, enum_labels)

            if enum_label:
                lines.append(f'  - {const_ref}: {enum_label}')
            else:
                val_type, val = decode_constant(const)
                if const.label:
                    lines.append(f'  - {const_ref} ({const.label}): {val} ({val_type})')
                else:
                    lines.append(f'  - {const_ref}: {val} ({val_type})')

    # Build semantic data flow description
    lines.append("")
    lines.append("DATA FLOW:")

    # Map terminal UIDs to their parent node/constant
    term_to_parent = _build_terminal_map(root)

    for wire in bd.wires:
        from_parent = term_to_parent.get(wire.from_term, wire.from_term)
        to_parent = term_to_parent.get(wire.to_term, wire.to_term)

        from_desc = _describe_terminal(from_parent, node_refs, uid_to_node, uid_to_const)
        to_desc = _describe_terminal(to_parent, node_refs, uid_to_node, uid_to_const)

        lines.append(f"  {from_desc} -> {to_desc}")

    # List SubVI dependencies
    if subvi_refs:
        lines.append("")
        lines.append("DEPENDENCIES (SubVIs called):")
        for ref in subvi_refs:
            lines.append(f"  - {ref}")

    return "\n".join(lines)


def _extract_enum_labels(root: ET.Element) -> dict[str, list[str]]:
    """Extract enum/ring labels from the XML."""
    enums = {}
    for multi_label in root.findall(".//*[@class='multiLabel']"):
        buf = multi_label.find("buf")
        if buf is not None and buf.text:
            # Format: (count)"label1""label2"...
            text = buf.text
            labels = []
            i = 0
            # Skip the count prefix like "(13)"
            if text.startswith("("):
                i = text.find(")") + 1
            # Parse quoted strings
            while i < len(text):
                if text[i] == '"':
                    end = text.find('"', i + 1)
                    if end > i:
                        labels.append(text[i + 1:end])
                        i = end + 1
                    else:
                        break
                else:
                    i += 1
            if labels:
                # Find parent UID
                parent = multi_label
                while parent is not None:
                    uid = parent.get("uid")
                    if uid:
                        enums[uid] = labels
                        break
                    parent = parent.find("..")  # This won't work, need different approach
                # Store by parent term UID if we can find it
                term_parent = root.find(f".//*[@class='term']/*[@class='bDConstDCO']/../..")
                if term_parent is not None:
                    term_uid = term_parent.get("uid")
                    if term_uid:
                        enums[term_uid] = labels
    return enums


def _get_enum_value_label(const: Constant, enum_labels: dict[str, list[str]]) -> str | None:
    """Get the label for an enum constant value."""
    try:
        if len(const.value) == 8:
            int_val = int(const.value, 16)
            # Check if this constant has associated enum labels
            if const.uid in enum_labels:
                labels = enum_labels[const.uid]
                if 0 <= int_val < len(labels):
                    return f"{labels[int_val]} (enum value {int_val})"
            # Check for system directory type pattern
            label_lower = (const.label or "").lower()
            if "system directory" in label_lower or int_val in SYSTEM_DIR_TYPES:
                dir_info = SYSTEM_DIR_TYPES.get(int_val)
                if dir_info:
                    name, win_env, unix_path = dir_info
                    return f"{name} (type {int_val}) -> Python: os.environ['{win_env}'] on Windows, '{unix_path}' on Unix"
    except (ValueError, TypeError):
        pass
    return None


def _build_terminal_map(root: ET.Element) -> dict[str, str]:
    """Map terminal UIDs to their parent node/constant UID."""
    term_map = {}

    # Find all terminals and map to parent
    for elem in root.iter():
        elem_uid = elem.get("uid")
        elem_class = elem.get("class", "")

        if elem_uid:
            # For prim and iUse nodes, map their terminals
            if elem_class in ("prim", "iUse", "whileLoop", "forLoop", "select"):
                for term in elem.findall("./termList/SL__arrayElement[@class='term']"):
                    term_uid = term.get("uid")
                    if term_uid:
                        term_map[term_uid] = elem_uid

            # For constant terminals (directly under term elements with bDConstDCO)
            if elem_class == "term":
                dco = elem.find("./dco[@class='bDConstDCO']")
                if dco is not None:
                    # This is a constant terminal - map it to itself
                    term_map[elem_uid] = elem_uid

    return term_map


def _describe_terminal(parent_uid: str, node_refs: dict, uid_to_node: dict, uid_to_const: dict) -> str:
    """Create a human-readable description of a terminal."""
    if parent_uid in node_refs:
        return node_refs[parent_uid]

    if parent_uid in uid_to_node:
        node = uid_to_node[parent_uid]
        if node.node_type == "iUse" and node.name:
            return f'"{node.name}"'
        elif node.node_type == "prim" and node.prim_res_id:
            prim_info = PRIMITIVE_MAP.get(node.prim_res_id)
            if prim_info:
                return prim_info[0]
        return f"node_{parent_uid}"

    if parent_uid in uid_to_const:
        const = uid_to_const[parent_uid]
        return f"const_{const.uid}"

    return f"term_{parent_uid}"


def _get_fp_terminal_names(bd_xml_path: Path) -> dict[str, str]:
    """Get front panel terminal names from the FPHb.xml file.

    Args:
        bd_xml_path: Path to the block diagram XML (*_BDHb.xml)

    Returns:
        Dict mapping DCO uid to terminal name
    """
    # Derive front panel XML path from block diagram path
    fp_xml_path = bd_xml_path.parent / bd_xml_path.name.replace("_BDHb.xml", "_FPHb.xml")

    if not fp_xml_path.exists():
        return {}

    try:
        tree = ET.parse(fp_xml_path)
        root = tree.getroot()

        names = {}
        # Find fPDCO elements (front panel data container objects)
        for fp_dco in root.findall(".//*[@class='fPDCO']"):
            uid = fp_dco.get("uid")
            if not uid:
                continue

            # Look for the label inside the ddo's partsList
            # Structure: fPDCO/ddo/partsList/SL__arrayElement[@class='label']/textRec/text
            ddo = fp_dco.find("ddo")
            if ddo is not None:
                # Try multiple paths where label might be
                label_elem = None
                for label in ddo.findall(".//*[@class='label']"):
                    text_elem = label.find(".//textRec/text")
                    if text_elem is not None and text_elem.text:
                        label_elem = text_elem
                        break

                if label_elem is not None and label_elem.text:
                    names[uid] = label_elem.text.strip('"')

        return names
    except Exception:
        return {}


def create_llm_prompt(summary: str, mode: str = "script", summary_format: str = "text") -> str:
    """Create a full prompt for the LLM to convert the VI to Python.

    Args:
        summary: VI summary from summarize() or cypher.from_blockdiagram()
        mode: "script" for standalone, "gui" for backend function
        summary_format: "text" or "cypher"

    Returns:
        Complete prompt string
    """
    if summary_format == "cypher":
        from .cypher import create_prompt
        return create_prompt(summary, mode)

    if mode == "gui":
        return f"""{summary}

Convert this LabVIEW VI to a Python backend function that will be called from a NiceGUI frontend.
- Create a single function that takes the INPUT controls as parameters
- Return the OUTPUT indicators as a tuple (or single value if one output)
- Use os.path for path operations
- Use os.makedirs with exist_ok=True for directory creation
- Map system directories to appropriate Python equivalents (e.g., APPDATA, HOME)
- The function should be importable (no if __name__ == '__main__')
- Output ONLY the Python code, no explanations."""

    return f"""{summary}

Convert this LabVIEW VI to an equivalent Python function.
- Use os.path for path operations
- Use os.makedirs with exist_ok=True for directory creation
- Map system directories to appropriate Python equivalents (e.g., APPDATA, HOME)
- Output ONLY the Python code, no explanations."""


# Backwards compatibility alias
def summarize_vi_cypher(bd_xml_path, main_xml_path=None):
    """Deprecated: Use cypher.from_blockdiagram() instead."""
    from .cypher import from_blockdiagram
    return from_blockdiagram(bd_xml_path, main_xml_path)
