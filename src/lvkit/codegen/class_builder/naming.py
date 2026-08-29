"""Name classification/conversion helpers for class generation."""

from __future__ import annotations


class _NamingMixin:
    """Constructor detection and class-name conversion."""

    def _is_constructor(self, method_name: str) -> bool:
        """Check if a method is a constructor-like method."""
        constructor_patterns = [
            "init",
            "new",
            "create",
            "construct",
        ]
        name_lower = method_name.lower()
        return any(p in name_lower for p in constructor_patterns)

    def _to_class_name(self, name: str) -> str:
        """Convert name to PascalCase class name.

        Preserves existing capitalization patterns (e.g., TestCase -> TestCase).
        """
        name = name.replace(".lvclass", "").replace(".LVCLASS", "")

        # If already looks like PascalCase (has uppercase letters), preserve it
        if any(c.isupper() for c in name) and not name.isupper():
            # Just remove spaces/dashes/underscores
            return name.replace("-", "").replace("_", "").replace(" ", "")

        # Convert from snake_case or kebab-case to PascalCase
        words = name.replace("-", " ").replace("_", " ").split()
        return "".join(word.capitalize() for word in words) or "LVClass"
