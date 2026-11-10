"""The third-party packages a generated package imports.

A catalog entry whose Python has no standard-library construct names the
standard package that has one (``"requires": ["npTDMS"]``). Generating a node of
such an entry records the package here, once for the whole package whichever
module the node is in, and the pipeline writes ``requirements.txt`` at the
package root, so ``pip install -r requirements.txt`` installs what the
generated code imports.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

REQUIREMENTS_FILE = "requirements.txt"


@dataclass
class PackageRequirements:
    """The packages the modules of one generated package require."""

    packages: set[str] = field(default_factory=set)

    def add(self, packages: Iterable[str]) -> None:
        self.packages.update(packages)

    def build(self) -> str | None:
        """The text of ``requirements.txt``: each package once, sorted, one per
        line; None when no module requires one."""
        if not self.packages:
            return None
        return "".join(f"{package}\n" for package in sorted(self.packages))

    def write(self, folder: Path) -> Path | None:
        """Write ``requirements.txt`` into ``folder`` when a module requires a
        package; the file written."""
        text = self.build()
        if text is None:
            return None
        path = folder / REQUIREMENTS_FILE
        path.write_text(text, encoding="utf-8")
        return path
