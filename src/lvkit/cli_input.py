"""The input path a command takes on the command line: a path that does not exist,
or a file of a kind the command does not take, is a usage error (exit 2) with a
message saying what the command takes, never a problem of the run."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from .run_report import EXIT_USAGE
from .vi_files import VI_EXTENSIONS, is_vi_file


@dataclass(frozen=True)
class InputKinds:
    """The inputs a command takes besides a VI file (``.vi``, ``.vim``, ``.vit``):
    the other file ``extensions`` (lower case), a VI's block-diagram heap
    (``*_BDHb.xml``) when ``heaps``, a folder when ``folders``."""

    extensions: tuple[str, ...] = ()
    folders: bool = False
    heaps: bool = True

    def takes(self, path: Path) -> bool:
        if path.is_dir():
            return self.folders
        return (
            is_vi_file(path)
            or (self.heaps and path.name.endswith("_BDHb.xml"))
            or path.suffix.lower() in self.extensions
        )

    @property
    def shown(self) -> str:
        """What the usage message names: every input the command takes."""
        names = [
            f"a VI file ({', '.join(VI_EXTENSIONS)})",
            *(f"a {extension}" for extension in self.extensions),
            *(["a block-diagram _BDHb.xml"] if self.heaps else []),
            *(["a folder"] if self.folders else []),
        ]
        if len(names) == 1:
            return names[0]
        return ", ".join(names[:-1]) + f" or {names[-1]}"


def input_usage_error(path: Path, kinds: InputKinds) -> int | None:
    """Exit code 2, with the usage message printed, when ``path`` does not exist
    or is not an input the command takes; None when it is."""
    if not path.exists():
        print(f"Error: Path not found: {path}", file=sys.stderr)
        return EXIT_USAGE
    if not kinds.takes(path):
        print(f"Error: expected {kinds.shown}: {path.name}", file=sys.stderr)
        return EXIT_USAGE
    return None


# The inputs of each command that takes a path.
DESCRIBE_INPUTS = InputKinds((".ctl",))
DIFF_INPUTS = InputKinds()
RENDER_INPUTS = InputKinds((".ctl",), folders=True)
VISUALIZE_INPUTS = InputKinds((".lvclass", ".lvlib"), folders=True)
DOCS_INPUTS = InputKinds((".ctl", ".lvclass", ".lvlib"), folders=True, heaps=False)
# generate, and unresolved (which runs generate).
GENERATE_INPUTS = InputKinds((".ctl", ".lvclass", ".lvlib", ".llb"), folders=True)
