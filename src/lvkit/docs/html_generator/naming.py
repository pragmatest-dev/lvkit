"""Naming/filename mixin for HTMLDocGenerator.

Methods: _extract_library_group, _extract_display_name,
_vi_name_to_filename, _class_name_to_filename.
"""

from __future__ import annotations


class NamingMixin:
    """Mixin providing VI/class name -> filename/grouping conversions."""

    def _extract_library_group(self, vi_name: str) -> str:
        """Extract library/group name from VI name for grouping.

        Examples:
            "GraphicalTestRunner.lvlib:Get Settings Path.vi"
                -> "GraphicalTestRunner.lvlib"
            "Build Path__ogtk.vi" -> "OpenG"
            "Get System Directory.vi" -> "vi.lib"
        """
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
        # Handle library-qualified names
        if ":" in vi_name:
            name = vi_name.split(":")[-1]
        else:
            name = vi_name

        # Remove .vi extension
        name = name.replace(".vi", "").replace(".VI", "")

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

        # Handle qualified names (Library.lvlib:VI.vi)
        safe_name = vi_name.replace(":", "_").replace("/", "_").replace("\\", "_")
        # Remove .vi extension if present
        safe_name = safe_name.replace(".vi", "").replace(".VI", "")
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
