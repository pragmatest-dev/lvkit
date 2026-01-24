# Known Issues and Deferred Work

This file tracks known issues and deferred work that needs to be addressed.

---

## Issue #1: Primitives Cannot Inject Import Dependencies

**Status:** Open
**Discovered:** 2026-01-24
**Severity:** Medium

### Problem

Primitives in `data/primitives-codegen.json` that use external modules (like `pickle`, `struct`, `json`) cannot inject their required imports into the generated module.

### Example

Primitive 1165 "Flatten To String" has:
```json
"python_code": "pickle.dumps() or struct.pack()"
```

This code:
1. Uses `pickle` and `struct` without importing them
2. Is also semantically wrong (doesn't take inputs, uses `or` incorrectly)

### Current Behavior

The `CodeFragment` class already supports an `imports` field:
```python
# In primitive.py line 168
imports: set[str] = set()
```

But this is never populated from primitive definitions.

### Proposed Fix

1. Add `"imports"` field to primitive schema in `primitives-codegen.json`:
   ```json
   "1165": {
     "name": "Flatten To String",
     "python_code": "flattened = pickle.dumps(data)",
     "imports": ["import pickle"],
     ...
   }
   ```

2. Update `PrimitiveCodeGen._build_string_hint()` and `_build_dict_hint()` to extract imports from resolved primitive and add to `CodeFragment.imports`

3. Fix primitive 1165's actual code to be correct

### Files to Modify

- `data/primitives-codegen.json` - Add imports field to primitives that need it
- `src/vipy/agent/codegen/nodes/primitive.py` - Extract and propagate imports
- `src/vipy/primitives.py` - Update schema/model if needed

---

## Issue #2: Class Input Detection Fails for Null-Named Terminals

**Status:** Open
**Discovered:** 2026-01-24
**Severity:** High

### Problem

LabVIEW class method inputs often have the name `'&#x00;'` (XML-escaped null character) instead of a meaningful name like "instance" or the class name.

When `to_var_name('&#x00;')` processes this, it falls back to returning `"instance"`.

The `_is_self_input()` method in `class_builder.py` checks the **original** name before transformation, so the filter fails and the parameter appears in the method signature.

### Example

```python
# Generated (wrong):
def cleanup(self, instance: Any) -> Any:

# Expected:
def cleanup(self) -> Any:
```

### Root Cause

1. Input has `name='&#x00;'` (null char)
2. `_is_self_input()` checks `'&#x00;'.strip() == "instance"` → False
3. Input passes through filter (not recognized as self)
4. `build_args()` calls `to_var_name('&#x00;')` → `"instance"`
5. Parameter appears as `instance: Any`

### Proposed Fix

Update `_is_self_input()` to detect null/invalid names that will become "instance":

```python
def _is_self_input(self, inp: Any, class_name: str) -> bool:
    # Check for null/empty names that to_var_name() converts to "instance"
    inp_name = inp.name if hasattr(inp, "name") else ""
    if not inp_name or inp_name in ('&#x00;', '\x00', 'None'):
        # Null-named input with class-compatible type is likely self
        # Check type to confirm
        ...
```

Or better: check by **type** first since the `lv_type` should indicate it's a class reference.

### Files to Modify

- `src/vipy/agent/codegen/class_builder.py` - Fix `_is_self_input()` detection

---

## Issue #3: Garbage Primitive Code for Flatten To String

**Status:** Open
**Discovered:** 2026-01-24
**Severity:** High
**Related:** Issue #1

### Problem

Primitive 1165 "Flatten To String" has nonsensical placeholder code:

```json
"python_code": "pickle.dumps() or struct.pack()"
```

This produces invalid Python that:
- Doesn't take any inputs
- Uses `or` which doesn't make sense here
- Produces repeated garbage lines in output

### Example Output

```python
def get_strings_from_enum__ogtk(enum: Any=None) -> ...:
    out_0, out_1, out_2 = pickle.dumps() or struct.pack()
    out_0, out_1, out_2 = pickle.dumps() or struct.pack()
    out_0, out_1, out_2 = pickle.dumps() or struct.pack()
    ...
```

### Proposed Fix

Replace with proper implementation:

```json
"1165": {
  "name": "Flatten To String",
  "python_code": "flattened_string = pickle.dumps({data})",
  "imports": ["import pickle"],
  "terminals": [
    {"index": 0, "direction": "out", "name": "flattened string"},
    {"index": 3, "direction": "in", "name": "data"}
  ]
}
```

Note: This won't be binary-compatible with LabVIEW's flatten format, but it will produce valid Python.

### Files to Modify

- `data/primitives-codegen.json` - Fix primitive 1165

---

## Adding New Issues

When deferring work, add an issue here with:
- Clear problem description
- Example of the bug/issue
- Root cause analysis
- Proposed fix
- Files that need modification
