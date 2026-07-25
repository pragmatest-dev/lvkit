"""Enable ``python -m lvkit`` — the invocation the VS Code extension uses when
running lvkit through a bundled (signed) Python interpreter, since a generated
console-script ``.exe`` gets blocked by Windows Application Control policies."""

from lvkit.cli import main

raise SystemExit(main())
