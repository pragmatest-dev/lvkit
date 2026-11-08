"""A path given to a command or MCP tool as lvkit prints it: ``<userlib>/…``,
``<vilib>/…`` or ``<instrlib>/…`` (a VI inside an ``.llb`` is named through its
archive). It is found in the configured library folders, in the order they are
tried; any other path is the path itself."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from .library_folders import is_symbolic_root, path_tokens

_FLAGS = {"<vilib>": "--vilib", "<userlib>": "--userlib", "<instrlib>": "--instrlib"}


class InputNotFound(Exception):
    """A stored path that names no file or folder here: the ``text`` given, the
    places it was ``tried`` and the ``reason``."""

    def __init__(self, text: str, tried: tuple[Path, ...], reason: str) -> None:
        super().__init__(f"{text}: {reason}")
        self.text = text
        self.tried = tried
        self.reason = reason


def is_stored_path(text: str) -> bool:
    """Whether ``text`` starts at a library folder (``<userlib>/…``)."""
    tokens = path_tokens(text)
    return bool(tokens) and is_symbolic_root(tokens[0])


def resolve_input(text: str, folders: Mapping[str, Sequence[Path]]) -> Path:
    """The path ``text`` names: a stored path is found under the first of its
    ``folders`` that has it; any other text is returned as the path it is."""
    if not is_stored_path(text):
        return Path(text)
    token, *rest = path_tokens(text)
    configured = folders.get(token, ())
    if not configured:
        flag = _FLAGS.get(token)
        reason = (
            f"no folder is configured for {token}: set {flag}"
            if flag
            else f"lvkit has no folder for {token}"
        )
        raise InputNotFound(text, (), reason)
    from .graph.llb_members import file_is_present  # reads archives: heavy import

    candidates = tuple(folder.joinpath(*rest) for folder in configured)
    for candidate in candidates:
        if candidate.is_dir() or file_is_present(candidate):
            return candidate
    raise InputNotFound(text, candidates, f"not found in any configured {token} folder")
