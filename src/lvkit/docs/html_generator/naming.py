"""Naming/filename mixin for HTMLDocGenerator.

Methods: _extract_library_group, _index_section, _extract_display_name,
_vi_short_label, _vi_name_to_filename, _class_name_to_filename,
_typedef_filenames.

A page's file name and folder come from ``docs/page_names.py``. The index page
lists each VI under a section of its own (``library_group``): its owning class or
library, else the LabVIEW installation folder its caller recorded, else its folder
in the project. The section never names a file.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path

from lvkit.docs.page_names import LIBRARY_GROUPS, PROJECT_GROUP, PageName
from lvkit.parser.models import is_symbolic_root
from lvkit.vi_files import strip_vi_extension


def library_group(owner: str | None, folders: Sequence[str]) -> str:
    """The index section of one VI: its owning class or library ``owner``; else the
    installation folder (``vi.lib``, ``user.lib``) its recorded path starts with
    (any other symbolic root by its own name); else its project-relative folder
    (``folders``, outermost first), ``Project`` when it sits in the project
    folder itself."""
    if owner is not None:
        return owner
    if folders and is_symbolic_root(folders[0]):
        return LIBRARY_GROUPS.get(folders[0], folders[0][1:-1])
    return "/".join(folders) or PROJECT_GROUP


class NamingMixin:
    """Mixin providing VI/class name -> filename/grouping conversions."""

    # VI name -> its page, for the VIs named by their file (``docs/page_names.py``).
    page_names: dict[str, PageName]
    # VI name -> its section on the index page (``library_group``).
    index_sections: dict[str, str]

    def _index_section(self, vi_name: str) -> str:
        """The section the index page lists ``vi_name`` under: the one registered
        for it (``register_index_sections``), else its page's folder."""
        return self.index_sections.get(vi_name) or self._extract_library_group(vi_name)

    def _vi_short_label(self, vi_name: str) -> str:
        """The name a list on a page shows for a VI: its file name, without the
        class or library that owns it (``Cls.lvclass:Do.vi`` is ``Do.vi``)."""
        return Path(vi_name.rsplit(":", 1)[-1]).name

    def _extract_library_group(self, vi_name: str) -> str:
        """Extract library/group name from VI name for grouping.

        Examples:
            "GraphicalTestRunner.lvlib:Get Settings Path.vi"
                -> "GraphicalTestRunner.lvlib"
            "Build Path__ogtk.vi" -> "OpenG"
            "Get System Directory.vi" -> "vi.lib"
        """
        if vi_name in self.page_names:
            return self.page_names[vi_name].group
        # Check for library-qualified name (Library.lvlib:VI.vi or lvclass:VI.vi)
        if ".lvlib:" in vi_name:
            return vi_name.split(":")[0]
        if ".lvclass:" in vi_name:
            return vi_name.split(":")[0]

        # Check for OpenG naming convention (__ogtk)
        if "__ogtk" in vi_name:
            return "OpenG"

        # Default to vi.lib for system VIs
        return "vi.lib"

    def _extract_display_name(self, vi_name: str) -> str:
        """Extract display name from full VI name.

        Examples:
            "GraphicalTestRunner.lvlib:Get Settings Path.vi" -> "Get Settings Path"
            "Build Path__ogtk.vi" -> "Build Path"
            "Get System Directory.vi" -> "Get System Directory"
        """
        # A VI that is a file is shown by its file name, never its path.
        if vi_name in self.page_names:
            name = Path(vi_name).name
        # Handle library-qualified names
        elif ":" in vi_name:
            name = vi_name.split(":")[-1]
        else:
            name = vi_name

        # Remove .vi extension
        name = strip_vi_extension(name)

        # Remove __ogtk suffix
        name = name.replace("__ogtk", "")

        return name.strip()

    def _vi_name_to_filename(self, vi_name: str) -> str:
        """Convert VI name to safe HTML filename with library subdirectory.

        Returns path like "OpenG/Build_Path_ogtk.html"
        or "vi.lib/Get_System_Directory.html"
        """
        # Get library group for subdirectory
        library_group = self._extract_library_group(vi_name)

        # Sanitize library group name for filesystem
        safe_lib = library_group.replace(".", "_").replace(":", "_").replace("/", "_")
        if vi_name in self.page_names:
            return f"{safe_lib}/{self.page_names[vi_name].stem}.html"

        # Handle qualified names (Library.lvlib:VI.vi)
        safe_name = vi_name.replace(":", "_").replace("/", "_").replace("\\", "_")
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
        safe_lib = classname.replace(".", "_").replace(":", "_").replace("/", "_")
        return f"{safe_lib}/{classname}.html"
