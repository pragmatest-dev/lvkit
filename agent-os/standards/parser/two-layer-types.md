# Two-Layer Type System

Types are represented differently in parser vs graph layers.

## ParsedType (parser layer)

Minimal type info from a single VI's XML. No external resolution.

```python
@dataclass
class ParsedType:
    kind: str  # "primitive", "cluster", "array", "typedef_ref"
    type_name: str  # "Path", "Cluster", "NumInt32"
    typedef_path: str | None  # For typedefs: path to .ctl
    typedef_name: str | None  # Qualified name
```

Location: `src/vipy/parser/models.py`

## LVType (graph layer)

Enriched type with resolved details (enum values, cluster fields, etc).

```python
@dataclass
class LVType:
    kind: str
    underlying_type: str | None
    values: dict[str, EnumValue] | None  # enum members
    fields: list[ClusterField] | None    # cluster fields
    element_type: LVType | None          # array element
```

Location: `src/vipy/graph_types.py`

## Conversion

Parser produces `ParsedType`. Graph layer (`memory_graph.py`) enriches to `LVType` using `vilib_resolver` for external typedef resolution.
