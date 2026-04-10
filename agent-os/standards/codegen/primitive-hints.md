# Primitive Code Hints

Primitive mappings live in `src/vipy/data/primitives.json`. Two formats exist.

## String Format (simple cases)

```json
{
  "1809": {
    "name": "Index Array",
    "python_code": "element = array[index]",
    "terminals": [...]
  }
}
```

For single-output primitives. Assignment target is stripped; expression assigned to wired output.

## Dict Format (complex cases)

```json
{
  "1234": {
    "name": "Some Primitive",
    "python_code": {
      "_body": "file.write(data)",
      "bytes_written": "len(data)"
    }
  }
}
```

- `_body`: Side effect statement executed first (optional)
- Other keys: Output terminal name -> expression

## Placeholder Substitution

Terminal names in code are replaced with resolved variable names:
- `array[index]` becomes `my_array[i]` if wired to those variables
- Unwired inputs become `None` (or `default_value` if specified)

## Terminal Definition

```json
"terminals": [
  {"index": 0, "direction": "out", "name": "result"},
  {"index": 1, "direction": "in", "name": "input", "default_value": "0"}
]
```
