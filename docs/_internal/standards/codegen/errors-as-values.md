# Errors as Values

A LabVIEW error cluster is a value on a wire, and the generated Python keeps it
one. The full design and its decisions are in
`docs/_internal/design/errors-as-values.md`.

## The value

- `None`: no error.
- `LabVIEWError(code, source, message, status)`: an error (`status` True) or a
  warning (`status` False, code set). The class is in the package's generated
  `errors.py` (`codegen/errors_module.py`); generated code imports nothing from
  lvkit.

## Generated pattern

```python
def my_vi(x, error_in=None):
    a = step_a(x=x, error_in=error_in)          # a call: the callee decides
    if a.error_out is None or not a.error_out.status:
        total = a.result + 1                    # skipped on an error, not a warning
    else:
        total = 0                               # skipped: the output's default
    return MyViResult(total=total, error_out=a.error_out)
```

## Rules

- A VI with error terminals takes `error_in` and returns a frozen result
  dataclass with `error_out`.
- A node is skipped when an error occurred on its own error in
  (`error_flow.guard`); its outputs take their defaults and its error out is the
  incoming error. Consecutive nodes on one error share one `if`.
- A node NI says runs despite an error runs always; its error out is the
  incoming error, else its own (first error wins).
- A native Python failure is the node's error out, with the code its catalog
  entry names, else 42 (`native_failure.py`); with no error out wired it stays
  an exception.
- Branches are plain sequential code with their own error values, so one
  branch's failure never stops another; Merge Errors takes the first error in
  input order, else the first warning.
- An error cluster wired to an input NI says takes only its status (And, Or,
  Not, Select's `s`, Compound Arithmetic, …) reads whether an error occurred
  (`error_flow.status_read`).
