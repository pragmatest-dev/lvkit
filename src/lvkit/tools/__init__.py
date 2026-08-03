"""Offline, corpus-driven audit tools for lvkit's data files.

These are NOT part of the conversion pipeline (parser -> graph -> codegen).
They read the bundled data files (``src/lvkit/data/*.json``) and the local
extraction cache to find corpus-wide inconsistencies, and are run manually
(or from a ``scripts/*.py`` CLI) as a review aid — never imported by
``pipeline.py`` or ``cli.py``.
"""

from __future__ import annotations
