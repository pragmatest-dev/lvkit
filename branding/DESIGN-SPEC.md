# PragmaTest brand family — design spec

One design language across **LVKit** (orange), **TesterKit** (green) and
**PragmaTest** (violet). This file is identical in all three repos; the
generator lives in the lvkit repo (`.tmp/branding_family.py`, shipped by
`.tmp/branding_family_ship.py`). Never restyle one brand alone — change the
generator and re-ship all three.

## Palette

| Role                  | Hex       | Use                                   |
|-----------------------|-----------|---------------------------------------|
| Slate (neutral dark)  | `#2b3038` | dark grounds, light-mode text         |
| Cream (neutral light) | `#faf7f2` | light grounds, dark-mode text         |
| LVKit orange          | `#e8821e` | LabVIEW DBL wire colour — LOCKED      |
| TesterKit green       | `#16a34a` | trust / pass                          |
| PragmaTest violet     | `#6741d9` | competence / modernity                |

Slate and cream are true inverses (swap for dark/light). All accents sit in one
saturation register; each brand uses exactly ONE accent.

## Marks

- 64×64 artboard, `stroke-width: 5`, round caps and joins, single accent colour.
- **Ink bbox exactly 54×44 units (x 5..59, y 10..54), ink-centred at (32,32).**
  Every mark fills the same box, so placed size, fill, margins, and line weight
  are identical across the family in every container (lockup, tile, favicon).
- Concepts — grounded in what each product does:
  - LVKit: orthogonal dataflow fan-out with square terminals + junction dot.
    The wiring vocabulary (terminals/junctions) is **LVKit-exclusive**.
  - TesterKit: measurement trace held between two spec-limit rails.
  - PragmaTest: flat-top hex certification seal with check.

## Wordmarks

- Type: **Selawik Bold** (OFL), outlined to paths — see FONTS.md. Cap height
  30 units, baseline y = 42, tracking −1.5.
- Two-tone: neutral-colour domain word + accent "kind" suffix
  (LV·**Kit**, Tester·**Kit**, Pragma·**Test**).
- Mark placement (identical for every brand): scale 0.72 → placed ink height
  31.68, vertical centre 27.04 (optical middle of the caps), ink left edge 5.6,
  mark→text gap 5.52.
- **Clear space: 0.4 × cap height = 12 units on ALL FOUR sides**, measured to
  the true ink bbox (mark ink ∪ outlined-glyph bounds). The SVG viewBox is
  framed exactly to it; nothing may enter it when placing the lockup.

## Exports (per brand, `{slug}` = lvkit / testerkit / pragmatest)

- `{slug}-mark.svg` (64×64) and `{slug}-mark-256.png` — transparent mark.
- `{slug}-icon-tile.svg` — 128×128, corner radius 28, slate ground, mark at
  1.5× (ink-centred). Rasterised as `{slug}-icon-128.png` / `{slug}-icon-256.png`.
- `favicon-16/32/48.png` — bare transparent mark.
- `{slug}-wordmark-light` (slate text on cream), `{slug}-wordmark-dark` (cream
  text on slate), `{slug}-wordmark` (cream text, transparent — the primary).
  SVG + PNG each; PNGs at 7 px per unit (cap = 210 px in every export).

## Rules

- Consistency is generator-enforced; don't hand-edit shipped SVGs.
- Clean-room: zero NI-derived artwork anywhere in the family.
