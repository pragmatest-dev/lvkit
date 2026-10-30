"""Naming/filename mixin for HTMLDocGenerator.

Methods: _extract_library_group, _extract_display_name,
_vi_name_to_filename, _class_name_to_filename, _typedef_filenames.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from lvkit.parser.models import is_symbolic_root
from lvkit.vi_files import strip_vi_extension

# The group of a VI that sits directly in the project folder.
PROJECT_GROUP = "Project"
# The folder each LabVIEW installation root names; any other symbolic root
# (``<resource>``) is named by its own token.
SYMBOLIC_ROOT_GROUPS = {
    "<vilib>": "vi.lib",
    "<userlib>": "user.lib",
    "<instrlib>": "instr.lib",
}


def group_of_symbolic_root(token: str) -> str:
    """The group of the VIs a caller recorded under the installation folder
    ``token`` (``<userlib>`` is ``user.lib``)."""
    return SYMBOLIC_ROOT_GROUPS.get(token, token[1:-1])


def folders_below_root(folders: Sequence[str]) -> tuple[str, ...]:
    """``folders`` without a leading installation folder, which the group names."""
    if folders and is_symbolic_root(folders[0]):
        return tuple(folders[1:])
    return tuple(folders)


def group_folder(group: str) -> str:
    """The folder a group's pages are written to."""
    return re.sub(r'[.:/\\<>|?*"]', "_", group)


def library_group(owner: str | None, folders: Sequence[str]) -> str:
    """The group of one VI: its owning class or library ``owner``; else the
    installation folder (``vi.lib``, ``user.lib``) its recorded path starts with;
    else its project-relative folder (``folders``, outermost first)."""
    if owner is not None:
        return owner
    if folders and is_symbolic_root(folders[0]):
        return group_of_symbolic_root(folders[0])
    return "/".join(folders) or PROJECT_GROUP


class NamingMixin:
    """Mixin providing VI/class name -> filename/grouping conversions."""

    # VI name -> the group its page is filed under (see ``library_group``).
    vi_groups: dict[str, str]
    # VI name -> the human-facing name shown for it (the qualified name or the
    # file name); a name not listed is shown as it is.
    vi_labels: dict[str, str]

    def _vi_label(self, vi_name: str) -> str:
        return self.vi_labels.get(vi_name, vi_name)

    def _vi_short_label(self, vi_name: str) -> str:
        """The VI's shown name without its owner (``Cls.lvclass:Do.vi`` is
        ``Do.vi``), for lists on a page."""
        return self._vi_label(vi_name).split(":")[-1]

    def _extract_library_group(self, vi_name: str) -> str:
        """The group a VI's page is filed under: the group registered for it in
        ``vi_groups``."""
        return self.vi_groups[vi_name]

    def _extract_display_name(self, vi_name: str) -> str:
        """The VI's shown name without its qualifier and ``.vi`` extension
        (``Lib.lvlib:Get Settings Path.vi`` is ``Get Settings Path``)."""
        label = self._vi_label(vi_name)
        return strip_vi_extension(label.split(":")[-1]).strip()

    def _vi_name_to_filename(self, vi_name: str) -> str:
        """Convert VI name to safe HTML filename with library subdirectory.

        Returns path like "Utility/Build_Path.html"
        or "Utility_error_llb/Get_System_Directory.html"
        """
        # Get library group for subdirectory
        library_group = self._extract_library_group(vi_name)

        # Sanitize library group name for filesystem
        safe_lib = group_folder(library_group)

        # Handle qualified names (Library.lvlib:VI.vi)
        safe_name = (
            self._vi_label(vi_name)
            .replace(":", "_")
            .replace("/", "_")
            .replace("\\", "_")
        )
        # Remove .vi extension if present
        safe_name = strip_vi_extension(safe_name)
        # Replace spaces and other unsafe characters with underscores
        safe_name = safe_name.replace(" ", "_").replace("(", "_").replace(")", "_")
        safe_name = (
            safe_name.replace("[", "_")
            .replace("]", "_")
            .replace("{", "_")
            .replace("}", "_")
        )
        safe_name = safe_name.replace("<", "_").replace(">", "_").replace("|", "_")
        safe_name = safe_name.replace("?", "_").replace("*", "_").replace('"', "_")
        # Remove any consecutive underscores
        while "__" in safe_name:
            safe_name = safe_name.replace("__", "_")

        return f"{safe_lib}/{safe_name}.html"

    def _typedef_filenames(self, entries: list[tuple[str, str]]) -> dict[str, str]:
        """``typedef/<name>.html`` for each ``(path key, display name)``. The path
        key is the identity: two controls that share a display name (a file name
        repeats across directories) each get a short hash of their path, so no
        two pages collide."""
        base_of = {
            key: re.sub(r"[^A-Za-z0-9_.-]+", "_", name.removesuffix(".ctl")).strip("_")
            or "typedef"
            for key, name in entries
        }
        counts: dict[str, int] = {}
        for base in base_of.values():
            counts[base] = counts.get(base, 0) + 1
        filenames = {}
        for key, base in base_of.items():
            if counts[base] > 1:
                base = f"{base}-{hashlib.sha1(key.encode()).hexdigest()[:8]}"
            filenames[key] = f"typedef/{base}.html"
        return filenames

    def _class_name_to_filename(self, classname: str) -> str:
        """Convert a class qualified name to its landing-page path.

        Lives in the same per-class subdirectory as its method pages
        (``_vi_name_to_filename`` groups a method VI's page under its
        owning class's sanitized qualified name), so intra-class links
        need no "../" prefix. The basename is the raw qualified class name
        with ".html" appended — this can never collide with a method page's
        basename, since those always carry a ":<method>.vi" suffix baked in
        before sanitizing.
        """
        safe_lib = group_folder(classname)
        return f"{safe_lib}/{classname}.html"
