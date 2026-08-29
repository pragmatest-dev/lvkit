"""lvkit.docs.html_generator — HTML documentation generator package.

Facade: re-exports HTMLDocGenerator so `from lvkit.docs.html_generator import
HTMLDocGenerator` keeps working unchanged for every existing importer.
"""

from .core import HTMLDocGenerator

__all__ = ["HTMLDocGenerator"]
