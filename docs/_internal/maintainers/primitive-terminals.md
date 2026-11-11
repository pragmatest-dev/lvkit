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

3. **Name each terminal with NI's label**
   - Take the label from NI's public page for the node (the terminal list in
     its Inputs/Outputs section), exactly as LabVIEW shows it, default
     parenthetical included (`error in (no error)`, `enable (T)`)
   - Match each label to its index by the terminal's type and by wiring in a
     real caller, never by the page's reading order
   - The Python name is made from the label (a default parenthetical dropped,
     `error in (no error)` -> `error_in`); write `"python_name"` on the
     terminal only when the derived name is a poor one for generated code
     (`x*y` derives `xy`, so `"python_name": "product"`)
   - `python_code` templates use `in_N`; a dict `python_code` is keyed by the
     output's Python name

4. **Update primitives.json**

```json
"8055": {
  "terminals": [
    {"index": 0, "direction": "output", "name": "new path"},
    {"index": 7, "direction": "input", "name": "path"},
    {"index": 8, "direction": "input", "name": "error in (no error)"}
  ],
  "python": {"_body": "Path(path).mkdir(...)"}
}
```

## Key rules

- `parmIndex` in XML = actual LabVIEW parameter index
- Bit 0 of `objFlags` = `isIndicator` = OUTPUT terminal
- A terminal's `name` is NI's label; its Python name is derived from it (or the
  terminal's `python_name`), and a dict `python_code` is keyed by that Python name

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
