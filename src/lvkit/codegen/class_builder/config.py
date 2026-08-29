"""Configuration for class generation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ClassConfig:
    """Configuration for class generation."""

    include_docstrings: bool = True
    use_dataclass: bool = False  # Use @dataclass for private data
    private_prefix: str = "__"  # Name-mangled prefix for LabVIEW private
    protected_prefix: str = "_"  # Single underscore for LabVIEW protected
