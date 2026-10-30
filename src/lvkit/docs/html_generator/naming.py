"""Naming/filename mixin for HTMLDocGenerator.

Methods: _extract_library_group, _extract_display_name,
register_vi_names, _vi_name_to_filename, _class_name_to_filename,
_typedef_filenames.
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


# A run of anything but these is not part of a file or folder name that is safe on
# every file system and in a link (``#`` and ``%`` would end or escape its path).
_UNSAFE_FILE_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_file_stem(text: str) -> str:
    """``text`` with every run of characters outside the safe set replaced by one
    ``_``: the one rule behind every page, icon and folder name."""
    return _UNSAFE_FILE_CHARS.sub("_", text)


def group_folder(group: str) -> str:
    """The folder a group's pages are written to."""
    return safe_file_stem(group.replace(".", "_"))


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
    # VI name -> its page path, unique per VI (see ``register_vi_names``); a VI not
    # listed gets the path its name gives.
    vi_pages: dict[str, str]

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

    def register_vi_names(
        self, groups: dict[str, str], labels: dict[str, str]
    ) -> None:
        """Set the group and the shown name of every VI and name each one's page.
        The path key is the identity: VIs whose names sanitize to one file name
        (``a b.vi`` and ``a_b.vi``; names that differ only in case) each get a
        short hash of their key, so no two pages collide."""
        self.vi_groups, self.vi_labels = groups, labels
        base_of = {key: self._vi_page_base(key) for key in sorted(groups)}
        counts: dict[str, int] = {}
        for base in base_of.values():
            counts[base.lower()] = counts.get(base.lower(), 0) + 1
        self.vi_pages = {
            key: (
                f"{base}-{hashlib.sha1(key.encode()).hexdigest()[:8]}.html"
                if counts[base.lower()] > 1
                else f"{base}.html"
            )
            for key, base in base_of.items()
        }

    def _vi_page_base(self, vi_name: str) -> str:
        """``<group folder>/<file name>`` of a VI's page, without the extension."""
        safe_name = re.sub(
            r"_+", "_", safe_file_stem(strip_vi_extension(self._vi_label(vi_name)))
        )
        return f"{group_folder(self._extract_library_group(vi_name))}/{safe_name}"

    def _vi_name_to_filename(self, vi_name: str) -> str:
        """The VI's page path with its group subdirectory, like
        ``Utility/Build_Path.html``."""
        return self.vi_pages.get(vi_name) or f"{self._vi_page_base(vi_name)}.html"

    def _typedef_filenames(self, entries: list[tuple[str, str]]) -> dict[str, str]:
        """``typedef/<name>.html`` for each ``(path key, display name)``. The path
        key is the identity: two controls that share a display name (a file name
        repeats across directories) each get a short hash of their path, so no
        two pages collide."""
        base_of = {
            key: safe_file_stem(name.removesuffix(".ctl")).strip("_") or "typedef"
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
        return f"{group_folder(classname)}/{safe_file_stem(classname)}.html"
