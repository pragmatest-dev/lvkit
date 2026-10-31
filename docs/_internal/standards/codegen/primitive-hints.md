# Primitive Code Hints

Primitive mappings live in `src/lvkit/data/primitives.json`. Two formats exist.

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
- An unwired input the code uses takes the terminal's `default` (the value NI documents, converted to Python as a constant of its type is) or the Python its generator writes for its `default_behavior`; with neither it is an `unknown_default` gap

## Terminal Definition

```json
"terminals": [
  {"index": 0, "direction": "out", "name": "result"},
  {"index": 1, "direction": "in", "name": "input", "default": 0}
]
```

`default` is a typed value (number, Boolean, string, or path text) read from NI's
public page for the function. `default_behavior` is one of the `DefaultBehavior`
members (`rest`, `append`, `one element`, ...) for a default NI documents that is no
value. A terminal with neither has no documented default.
