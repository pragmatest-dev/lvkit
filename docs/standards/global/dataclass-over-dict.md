# Dataclass Over Dict

Use typed dataclasses for structured data, not raw dicts.

## Why

- `obj.field` has IDE autocomplete; `dict["key"]` doesn't
- Type hints catch errors at edit time
- Explicit field definitions document the structure

## Do

```python
@dataclass
class Operation:
    id: str
    name: str | None
    terminals: list[Terminal]

op.name  # IDE knows this is str | None
```

## Don't

```python
op = {"id": "123", "name": "foo", "terminals": []}
op["name"]  # IDE has no idea what type this is
```

## Canonical Types

- `src/lvkit/graph_types.py` - Graph layer (Operation, Wire, LVType, etc.)
- `src/lvkit/parser/models.py` - Parser layer (ParsedType, BlockDiagram, etc.)
