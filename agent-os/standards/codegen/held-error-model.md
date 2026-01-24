# Held Error Model

LabVIEW continues executing parallel branches after one errors. Python exceptions would stop immediately. The held error model preserves LabVIEW semantics.

## When It Applies

Enabled when BOTH conditions are true:
1. VI has parallel branches
2. VI has error cluster terminals (input or output)

VIs without error terminals use natural Python exceptions.

## Generated Pattern

```python
def my_vi(input_data):
    _held_error = None

    # Branch 0
    try:
        branch_0_result = branch_0_ops()
    except LabVIEWError as e:
        _held_error = _held_error or e
        branch_0_result = None

    # Branch 1
    try:
        branch_1_result = branch_1_ops()
    except LabVIEWError as e:
        _held_error = _held_error or e
        branch_1_result = None

    # Raise first error at merge point
    if _held_error:
        raise _held_error

    return result
```

## Key Details
- `_held_error or e` preserves first error, ignores later ones
- Results set to `None` on branch failure
- Error raised at merge point, not immediately
- Import: `from vipy.labview_error import LabVIEWError`
