"""Unified naming utilities for LabVIEW artifacts.

Provides functions to build:
1. Relative paths - filesystem location (forward slashes)
2. Qualified names - namespace/ownership chain (colon for containers)

Containers (.llb, .lvlib, .lvclass) can own files and nest inside each other.
"""

from __future__ import annotations


def build_relative_path(path_tokens: list[str]) -> str:
    """Build relative filesystem path from tokens.

    Args:
        path_tokens: List of path components

    Returns:
        Forward-slash separated path string

    Example:
        ['Utility', 'sysdir.llb', 'System Directory Type.ctl']
        → 'Utility/sysdir.llb/System Directory Type.ctl'
    """
    return '/'.join(path_tokens)


def build_qualified_name(owner_chain: list[str], item_name: str) -> str:
    """Build qualified name from explicit ownership chain.

    The owner_chain comes from XML metadata (LIBN section), NOT path inference.
    Path and qualified name are independent - a file can be at any filesystem
    location while belonging to a specific library namespace.

    Args:
        owner_chain: List of container names from outermost to innermost
                    e.g., ['MyLib.lvlib', 'MyClass.lvclass']
        item_name: The VI or .ctl filename

    Returns:
        Colon-separated qualified name

    Examples:
        owner_chain=[], item='Standalone.vi'
        → 'Standalone.vi'

        owner_chain=['sysdir.llb'], item='System Directory Type.ctl'
        → 'sysdir.llb:System Directory Type.ctl'

        owner_chain=['MyLib.lvlib', 'MyClass.lvclass'], item='Method.vi'
        → 'MyLib.lvlib:MyClass.lvclass:Method.vi'
    """
    if not owner_chain:
        return item_name
    return ':'.join(owner_chain) + ':' + item_name


def parse_qualified_name(qualified_name: str) -> tuple[list[str], str]:
    """Parse a qualified name into owner chain and item name.

    Args:
        qualified_name: Colon-separated qualified name

    Returns:
        Tuple of (owner_chain, item_name)

    Examples:
        'Standalone.vi' → ([], 'Standalone.vi')
        'sysdir.llb:Type.ctl' → (['sysdir.llb'], 'Type.ctl')
        'Lib.lvlib:Class.lvclass:Method.vi'
            → (['Lib.lvlib', 'Class.lvclass'], 'Method.vi')
    """
    parts = qualified_name.split(':')
    if len(parts) == 1:
        return [], parts[0]
    return parts[:-1], parts[-1]
