"""Default value parsing from DFDS section.

Handles parsing default fill data for controls and indicators.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .models import ParsedDefaultValue


def parse_dfds(xml_path: Path | str) -> dict[int, ParsedDefaultValue]:
    """Parse the DFDS (Default Fill of Data Space) section for default values.

    Args:
        xml_path: Path to the main .xml file (not BDHb/FPHb)

    Returns:
        Dict mapping TypeID to ParsedDefaultValue with parsed values
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    defaults: dict[int, ParsedDefaultValue] = {}

    for data_fill in root.findall(".//DFDS//DataFill"):
        type_id_str = data_fill.get("TypeID")
        if not type_id_str:
            continue
        type_id = int(type_id_str)

        values, structure = _parse_data_fill(data_fill)
        if values is not None:
            defaults[type_id] = ParsedDefaultValue(
                type_id=type_id,
                values=values,
                structure=structure,
            )

    return defaults


def _parse_data_fill(elem: ET.Element) -> tuple[list[Any] | None, str]:
    """Parse a DataFill element and extract values."""
    cluster = elem.find("Cluster") or elem.find("SpecialDSTMCluster/Cluster")
    if cluster is not None:
        values = []
        for child in cluster:
            val = _parse_value_element(child)
            if val is not None:
                values.append(val)
        return values, "Cluster"

    array = elem.find("Array") or elem.find("SpecialDSTMCluster/Array")
    if array is not None:
        dim = array.find("dim")
        dim_val = int(dim.text) if dim is not None and dim.text else 0
        values = []
        for child in array:
            if child.tag != "dim":
                val = _parse_value_element(child)
                if val is not None:
                    values.append(val)
        return values, f"Array[{dim_val}]"

    for child in elem:
        val = _parse_value_element(child)
        if val is not None:
            return [val], "scalar"

    return None, "unknown"


def _parse_value_element(elem: ET.Element) -> Any:
    """Parse a single value element (Boolean, I32, DBL, String, etc.)."""
    tag = elem.tag

    if tag == "Boolean":
        return elem.text == "1" if elem.text else False
    elif tag in ("I32", "I16", "I8", "U32", "U16", "U8"):
        return int(elem.text) if elem.text else 0
    elif tag in ("DBL", "SGL", "EXT"):
        return float(elem.text) if elem.text else 0.0
    elif tag == "String":
        return elem.text or ""
    elif tag == "Path":
        path_str = elem.find("String")
        return path_str.text if path_str is not None and path_str.text else ""
    elif tag == "Cluster":
        values = []
        for child in elem:
            val = _parse_value_element(child)
            if val is not None:
                values.append(val)
        return values
    elif tag == "Array":
        values = []
        for child in elem:
            if child.tag != "dim":
                val = _parse_value_element(child)
                if val is not None:
                    values.append(val)
        return values
    elif tag == "RepeatedBlock":
        values = []
        for child in elem:
            val = _parse_value_element(child)
            if val is not None:
                values.append(val)
        return values
    elif tag == "Block":
        return elem.text or ""

    return None
