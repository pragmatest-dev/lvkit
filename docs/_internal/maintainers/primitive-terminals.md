# Discovering Primitive Terminal Indices from XML

When adding a new primitive to `src/lvkit/data/primitives.json`, the terminal
indices must come from the actual LabVIEW XML — not guessed.

## Steps

1. **Find a VI that uses the primitive**

```bash
find samples -name "*_BDHb.xml" -exec grep -l "primResID>8055" {} \;
```

2. **Extract terminal data from XML**

```python
for elem in root.iter():
    prim_res_id = elem.find("primResID")
    if prim_res_id is not None and prim_res_id.text == "8055":
        term_list = elem.find("termList")
        for term in term_list.findall("SL__arrayElement"):
            dco = term.find("dco")
            # Get parmIndex from dco element
            parm_idx_elem = dco.find("parmIndex")
            parm_idx = int(parm_idx_elem.text) if parm_idx_elem else list_position

            # Get direction from bit 0 of combined flags
            flags = term_flags | dco_flags
            is_output = bool(flags & 0x1)  # Bit 0 = isIndicator = output
```

3. **Map terminals to names**
   - Look at the hint variables (e.g., `path`, `error_in`)
   - Match input indices to hint input variables
   - Match output indices to hint output variables

4. **Update primitives.json**

```json
"8055": {
  "terminals": [
    {"index": 0, "direction": "out", "name": "new path"},
    {"index": 7, "direction": "in", "name": "path"},
    {"index": 8, "direction": "in", "name": "error in"}
  ],
  "python": {"_body": "Path(path).mkdir(...)"}
}
```

## Key rules

- `parmIndex` in XML = actual LabVIEW parameter index
- Bit 0 of `objFlags` = `isIndicator` = OUTPUT terminal
- Terminal names must match the variables used in `python` hints

## When several terminals share a type (step 3 is ambiguous)

The `<dco>` gives you parmIndex + direction for every terminal, but when two or
more terminals share a type it cannot say which parmIndex plays which **role** —
types alone don't order same-typed terminals. (1540 Array To Spreadsheet String
is the canonical case: `delimiter`, `format string`, and `array` are all
`String`-ish inputs.) Two clean-room resolvers, in preference order:

1. **Caller wiring** — find a caller that wires each terminal to a *named*
   control (e.g. OpenG "1D Array to String" wires 1540's terminals to named
   controls). The signal→terminal-UID references pin role→parmIndex. Preferred
   when such a caller exists.
2. **Glyph terminal-role detection** — read NI's public connector-pane doc image:
   the icon's pixel box maps 1:1 to `termBounds`, so each wire's drop-in point
   yields its parmIndex and the wire's far label yields its role. Works with no
   caller, for any primitive whose doc page shows a wired connector pane. See
   `docs/_internal/design/glyph-terminal-role-detection.md`.
