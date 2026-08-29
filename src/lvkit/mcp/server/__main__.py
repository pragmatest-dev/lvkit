"""``python -m lvkit.mcp.server`` entry point.

The pre-split single-file module (``lvkit/mcp/server.py``) supported this
invocation via its own ``if __name__ == "__main__": main()`` guard, which a
plain module — but not a package — picks up automatically. Now that
``lvkit.mcp.server`` is a package, that invocation style needs this
``__main__.py`` to keep working.
"""

from __future__ import annotations

from . import main

if __name__ == "__main__":
    main()
