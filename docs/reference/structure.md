# structure

List the members of a `.lvlib` or `.lvclass` — a class's private data control
and methods (scope, static), or a library's version and members — or discover
the libraries, classes, and standalone VIs under a directory. All without
opening LabVIEW.

## Synopsis

```bash
lvkit structure <input> [options]
```

## Arguments

| Argument | Description |
|----------|-------------|
| `input` | A directory, `.lvlib`, or `.lvclass` file. |

## Options

| Option | Description |
|--------|-------------|
| `--json` | Output as JSON instead of text. The JSON fields differ by `input` type — see the examples below. |
| `--plan` | **Directory `input` only.** Print a Python module/package plan instead of the summary counts — see [Structure plan](#structure-plan). On a `.lvlib` or `.lvclass` input it has no effect: the ordinary text or JSON output prints instead, with no warning that `--plan` was ignored. |

`structure` does not take the SubVI resolution flags (`--search-path`,
`--project-root`, `--vilib`, `--userlib`, `--no-auto-vilib` — see
[SubVI & vi.lib resolution](subvi-resolution.md)): it only inspects
library/class membership, not block-diagram wiring, so it needs no `.lvkit/`
resolution store and no prior [`setup`](setup.md) run.

## Example: class

```bash
lvkit structure "Result Logger.lvclass"
```

```text
Class: Result Logger
  Private Data: Result Logger.ctl
  Methods:
    - Result Logger_New (protected) [static]
    - Result Logger_GetAttributes (protected) [static]
    - Message to Array (public)
    - Result Logger_Create (public) [static]
    - Destroy (public)
    - Setup Log (public)
    - Close Log (public)
    - Get Log - All (public)
    - Get Log - Last (public)
    - Save Log - Single Entry (public)
    - Message to Array - Test Results (public)
```

A subclass prints an `Inherits: <parent class>` line above `Private Data`.
`--json` prints the same information as `name`, `path`, `parent_class`,
`private_data`, and a `methods` list (each with `name`, `scope`, `is_static`,
`vi_path`).

## Example: library

```bash
lvkit structure "VITesterUtilities.lvlib"
```

```text
Library: VITesterUtilities
  Version: 1.0.0.0
  Members (46):
    - Get LVClass Name from TD.vi [VI]
    - Load Test Case from File.vi [VI]
    - Get LVClass Name from Data String.vi [VI]
    ...
```

`--json` prints `name`, `path`, `version`, and a `members` list (each with
`name`, `type`, `url`):

```json
{
  "name": "Graphical Diff",
  "path": "Graphical Diff.lvlib",
  "version": "1.0.0.0",
  "members": [
    {"name": "subVIs", "type": "Folder", "url": ""},
    {"name": "run_diff.vi", "type": "VI", "url": "../run_diff.vi"}
  ]
}
```

## Example: directory

```bash
lvkit structure "Result Logger_class"
```

```text
Project Structure: Result Logger_class
  Libraries: 0
  Classes: 1
  Standalone VIs: 2

Classes:
  - Result Logger (11 methods)
```

`--json` prints the full discovery structure: `libraries` and `classes` lists
(each entry shaped like the single-library/single-class JSON above) plus a
`standalone_vis` list of VI paths relative to `input`.

## Structure plan

`--plan` walks a directory's discovered libraries and classes and prints a
proposed Python module/package layout, independent of what
[`generate`](generate.md) actually names its output (see
[Output layout](generate.md#output-layout)): one module per library, one
Python class per `.lvclass` (subclassing its parent class when it has one),
and one function or method per VI — `@staticmethod` for a static method —
with LabVIEW names converted to `snake_case` (or `PascalCase` for class
names) Python identifiers. `--plan` only applies to a directory `input`; see
[Options](#options).

```bash
lvkit structure "Result Logger_class" --plan
```

```text
# Python Structure Plan

## Classes -> Python Classes

### Result Logger -> class ResultLogger:
Path: Result Logger.lvclass
Instance data: Result Logger.ctl
Methods:
  - @staticmethod __result_logger_new()
  - @staticmethod __result_logger_getattributes()
  - message_to_array()
  - @staticmethod result_logger_create()
  - destroy()
  - setup_log()
  - close_log()
  - get_log___all()
  - get_log___last()
  - save_log___single_entry()
  - message_to_array___test_results()
```

A private method's name is prefixed `_`, a protected method's `__` — that's
where `__result_logger_new` and `__result_logger_getattributes` (both
`protected` in the class example above) come from.

## Failures

A missing `input` prints `Error: Path not found: <path>` and exits 1. An
`input` that is neither a directory nor a `.lvlib`/`.lvclass` file prints
`Error: Unsupported file type: <path>` and exits 1.

## See also

- [describe](describe.md) — the signature and operations of a single method VI.
- [docs](docs.md) — full cross-linked HTML documentation for a library or class.
- [generate](generate.md) — convert the VIs `structure --plan` maps out into actual Python source.
