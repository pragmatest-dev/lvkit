# Wordmark typography

The **LVKit** wordmark is set in **Selawik Bold** and shipped as **outlined
vector paths** (not live `<text>`), so it renders identically in every browser
and rasteriser — no font dependency, pixel-stable at any size.

- **Selawik** is Microsoft's **open, metric-compatible substitute for Segoe UI**,
  released under the **SIL Open Font License 1.1** (© 2015 Microsoft Corporation).
  The OFL permits use, embedding, and derivative works — including outlining
  glyphs into a logo. We ship the outlines, not the font file.
- Source: https://github.com/microsoft/Selawik (release 1.01, `selawkb.ttf`).

The whole PragmaTest family (LVKit, TesterKit, PragmaTest) shares this
typography — see DESIGN-SPEC.md. To regenerate, re-run
`.tmp/branding_family_ship.py` in the lvkit repo.

_Not legal advice._
