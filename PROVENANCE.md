# Provenance

This document records how lvkit's knowledge of the LabVIEW VI format and of
LabVIEW primitive / vi.lib semantics was obtained. It exists to make lvkit's
clean-room posture explicit and verifiable.

## Summary

lvkit was developed **using only publicly available information**. During its
development:

- **No** LabVIEW or NI software was installed or run.
- **No** NI source code was accessed or used.
- **No** NI internal, non-public, or confidential/proprietary specifications or
  materials were accessed or used.
- **No** NI documentation prose or artwork is copied into or redistributed by
  lvkit.

lvkit is an independent project and is not affiliated with, authorized by,
endorsed by, or sponsored by National Instruments Corporation. LabVIEW, NI, and
National Instruments are trademarks of National Instruments Corporation.

## The only two inputs

Every piece of lvkit's understanding of the VI format and of LabVIEW semantics
comes from exactly two public sources, and nothing else:

1. **pylabview** — <https://github.com/mefistotelis/pylabview>, an independent,
   open-source project that parses the VI binary container format and extracts
   it to XML. lvkit **depends on** pylabview as an ordinary package dependency;
   it does not vendor or copy it. The XML that pylabview emits is lvkit's *only*
   window into the on-disk format — lvkit has no other view of the binary.

2. **Public NI documentation** — pages published openly on ni.com
   (e.g. the online LabVIEW reference at docs.ni.com / docs-be.ni.com), accessed
   with **no login, partner portal, or NDA gate**. These are consulted only to
   confirm the *identity and documented behavior* of primitives and
   standard-library VIs. Only facts — names, terminal roles/indices, documented
   behavior — are used. No documentation text or images are copied or
   redistributed. NI documentation is retrieved at runtime by the (optional)
   resolver skills, on the user's own machine; it is not bundled with lvkit.

## The primitive / vi.lib mappings are inferred, not authoritative

lvkit's mappings (in `src/lvkit/data/primitives.json` and
`src/lvkit/data/vilib/`) are **lvkit's own definitions**, produced **by
inference** from the two public inputs above. They are:

- **Open source and fully inspectable** — anyone can read exactly what lvkit
  believes each primitive/VI does and how that belief was formed.
- **Annotated with their reasoning** where applicable — many entries carry a
  note recording the public doc consulted, the observed terminal signature, or a
  `verified` / `guess_reason` marker.
- **Best-effort, not authoritative** — the mappings have been incorrect and
  corrected over time. That imperfection is a direct and expected consequence of
  having only public documentation and the pylabview XML to work from: an author
  with access to an internal or authoritative NI specification would not need to
  infer, and would not produce the guesses and later corrections that lvkit's
  history shows.

In other words, the open, inferential, and occasionally-wrong nature of these
definitions is itself evidence that they were derived from public information
and observation — not copied from any internal NI source.

## Not used / removed

- No NI-created artwork. Primitive glyphs are drawn procedurally by the render
  layer; the earlier NI-derived icon path was removed, and the packaging
  excludes any such asset.
- No NI documentation PDF or prose is bundled. Documentation is consulted from
  public ni.com pages at development/resolution time only.
