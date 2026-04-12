# TypeID Resolution

LabVIEW XML uses TypeID references. Resolution happens in `type_mapping.py`.

## API

```python
from lvkit.parser.type_mapping import parse_type_map_rich

type_map: dict[int, LVType] = parse_type_map_rich(xml_path)
lv_type = type_map.get(type_id)  # Returns LVType or None
```

## What It Resolves

- Primitives: `NumInt32`, `String`, `Boolean`, `Path`
- Clusters: With field names and nested types
- Arrays: With element type and dimensions
- Enums: With member names and values
- TypeDefs: Path to `.ctl` and underlying type
- Refnums: Queue, Notifier, class references

## Input

Requires the main `.xml` file (not BDHb/FPHb). Parses both XML comments and VCTP section.
