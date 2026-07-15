# Changelog

lvkit follows semantic versioning.

## [0.4.0]
- ***Block-diagram renderer:** `lvkit render <vi> -o out.svg` — produces a headless
  block-diagram SVG with interactive frames and procedural primitive shapes.
- Known limitation: a standalone `.vi` may under-resolve types (e.g. cluster
  field names) — render with `--search-path` / a project for full fidelity.

## [0.3.0]
- Formula Node support with LabVIEW-validated numeric semantics.

## [0.2.0]
- Published to PyPI; `lvkit setup`, visualization extra.

## [0.1.0]
- Initial release.
