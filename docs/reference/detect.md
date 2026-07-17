# detect

Detect a locally installed LabVIEW and its `vi.lib`/`user.lib` directories.

## Synopsis

```bash
lvkit detect [options]
```

## Options

| Option | Description |
|--------|-------------|
| `--json` | Output the detection result as JSON. |

`detect` takes no arguments and no [SubVI resolution flags](subvi-resolution.md).

## Example

```bash
lvkit detect
```

When nothing is found:

```text
No local LabVIEW detected. Pass --vilib <path-to-vi.lib> explicitly to resolve <vilib> dependency refs.
```

When an install is found, `detect` prints a header line and four labeled
fields:

```text
Detected LabVIEW 2021
  install dir : /usr/local/natinst/LabVIEW-2021-64
  vi.lib      : /usr/local/natinst/LabVIEW-2021-64/vi.lib
  user.lib    : /usr/local/natinst/LabVIEW-2021-64/user.lib
  source      : linux-glob
```

The header reads `Detected LabVIEW (unknown version)` if no version string was
recorded. `user.lib` reads `(not found)` if that directory doesn't exist.

`detect` always exits `0`, whether or not an install was found — check stdout
(or `--json`) for status, not the exit code.

## `--json` output

`--json` prints one of two shapes. Nothing found:

```json
{
  "detected": false
}
```

An install found:

```json
{
  "detected": true,
  "version": "2021",
  "install_dir": "/usr/local/natinst/LabVIEW-2021-64",
  "vilib_root": "/usr/local/natinst/LabVIEW-2021-64/vi.lib",
  "userlib_root": "/usr/local/natinst/LabVIEW-2021-64/user.lib",
  "source": "linux-glob"
}
```

`version` and `userlib_root` are `null` when unknown or absent. `source`
identifies how the install was located:

| `source` | Meaning |
|----------|---------|
| `windows-registry:CurrentVersion` | Windows, from the registered default install (`HKLM\SOFTWARE\National Instruments\LabVIEW\CurrentVersion`). |
| `windows-registry:highest` | Windows, no default registered — the highest version found under the registry. |
| `macos-glob` | macOS, found under a fixed `/Applications/.../LabVIEW */` path. |
| `linux-glob` | Linux, found under `/usr/local/natinst/LabVIEW-*/`. |

## Notes

- Other commands use this same detection automatically to resolve
  `<vilib>`/`<userlib>` references when `--vilib`/`--userlib` aren't given,
  unless `--no-auto-vilib` is passed — see
  [SubVI & vi.lib resolution](subvi-resolution.md).
- Detection only checks the current platform's fixed locations — the Windows
  registry, or on macOS/Linux the glob paths listed above. It never scans the
  filesystem, so an install outside those locations reports as not detected
  even if LabVIEW is present.
- When multiple versions are installed side by side, `detect` prefers the
  registered default (`windows-registry:CurrentVersion`); otherwise it takes
  the highest version found. A candidate whose `vi.lib` doesn't exist on disk
  is dropped, so a stale registry entry for an uninstalled version is skipped
  automatically.
- Auto-detection makes results depend on what's installed on the machine
  running lvkit. For reproducible runs (CI, code review), pass
  `--no-auto-vilib` to the other commands instead of relying on `detect`.

## See also

- [SubVI & vi.lib resolution](subvi-resolution.md) — how `--vilib`, `--userlib`, and `--no-auto-vilib` fit together.
- [setup](setup.md) — cache resolved vi.lib VIs into the project's `.lvkit/` store.
