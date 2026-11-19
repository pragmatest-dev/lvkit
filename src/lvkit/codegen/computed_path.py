"""A path the diagram computes when the code runs, found among the project's
files: read as a ``/`` path from the folder of the module being written, and
looked up in a generated dictionary keyed by the paths of the project's files from
that folder (computed VI paths and class paths)."""

from __future__ import annotations

import os
import posixpath
from pathlib import Path

from .context import CodeGenContext
from .import_resolver import ImportResolver

# The path in ``{value}`` as a ``/`` path from the folder of the module (an
# absolute path is kept absolute, so it is as far from it as it is).
PATH_FROM_HERE = """\
{here} = os.path.dirname(__file__)
{path} = os.path.join({here}, os.fspath({value}).replace("\\\\", "/"))
{rel} = os.path.relpath({path}, {here}).replace(os.sep, "/")
"""


def computed_path_module_key(ctx: CodeGenContext) -> str:
    """The key of the module being written: a path computed when the code runs
    is read from its folder (the resolver's ``module_key``: a class, a
    polymorphic VI, the VI itself)."""
    assert ctx.graph is not None and ctx.vi_name is not None
    resolver = ctx.import_resolver
    if isinstance(resolver, ImportResolver) and resolver.module_key:
        return resolver.module_key
    return ctx.graph.owning_class_key(ctx.vi_name) or ctx.vi_name


def path_from(resolver: object, key: str, module_key: str) -> str:
    """The path of the file ``key`` from the folder of the module generated for
    ``module_key``, both by the project-relative paths modules are placed by."""
    if isinstance(resolver, ImportResolver):
        member = resolver.shown(key)
        folder = posixpath.dirname(resolver.shown(module_key))
        return posixpath.relpath(member, folder or ".")
    return Path(os.path.relpath(key, os.path.dirname(module_key))).as_posix()
