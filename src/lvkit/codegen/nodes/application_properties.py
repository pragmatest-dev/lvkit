"""The facts about the application a Property Node of the Application class reads
(``default_objects.json``, class ``App``), and Owning Application of a VI.

A generated package is a program of its own, so each fact is the program's, or a
fact of the VI file it was made from:

- Application:Version Number (App.Version) and App.VersionYearNumberOnly: the
  LabVIEW version the VI was saved in, written at generation from the file
  (``VIProperties.lv_version``, "Major.Minor.Bugfix"), the version as a string
  and its year as a number. NI: "Version number of the application"
  (application/version.html), "Version year of the application"
  (application/versionyear.html). Since 2009 a major version number is the
  calendar year of its release minus 2000 (LabVIEW 2009 is 9.0, 2020 is 20.0;
  NI's LabVIEW 2009 SP1 readme lists 9.0.1; LabVIEW Artisan, "2009 Reasons I
  Love the New LabVIEW Versioning Scheme":
  https://labviewartisan.blogspot.com/2009/05/2009-reasons-i-love-new-labview.html),
  so the year is
  ``2000 + major`` for a major of 9 or more; a file saved by an earlier LabVIEW
  (8.6 and before) names no year, and a file that records no version names
  neither: the row is a gap naming the VI.
- App.Kind: 2, Run-Time System (NI, application/appkind.html: "2 Run Time
  System"): a generated package is a stand-alone program.
- Application:Name, App.ContextName, App.AllContexts, App.DefaultApplication and
  Owning Application: the ``application`` object of the package's ``panels``
  module; Application:All VIs In Memory: ``loaded_vis()`` of the same module
  (``codegen/panels_application.py``).

A row on a wired Application reference reads the same expression, with
``{application}`` the reference.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode

from ..ast_utils import parse_expr
from ..context import CodeGenContext
from ..default_objects import DefaultObjectProperty
from ..panels_application import APPLICATION_NAME

# The code a Property Node of the VI class records beside Owning Application
# (``OwningApp``; NI: properties-and-methods/vi-server/vi/owningapp.html).
OWNING_APP_PROPERTY_CODE = 623

# The first year a LabVIEW major version number is: 2009 is 9.0.
_FIRST_YEAR_MAJOR = 9
_YEAR_BASE = 2000


def owning_app() -> ast.expr:
    """Owning Application of a VI: the one application object."""
    return parse_expr(APPLICATION_NAME)


def _saved_version(ctx: CodeGenContext, node: PrimitiveNode) -> str:
    """The LabVIEW version ("Major.Minor.Bugfix") the generating VI was saved in."""
    version = (
        ctx.graph.get_vi_properties(ctx.vi_name or "").lv_version
        if ctx.graph is not None
        else None
    )
    if version is None:
        raise ConversionGap(
            "The VI's file does not record the LabVIEW version it was saved in", node
        )
    return version


def _saved_year(version: str, node: PrimitiveNode) -> int:
    """The year of the LabVIEW release ``version`` is; a gap for a version before
    2009, which has no year in its number."""
    major = int(version.split(".")[0])
    if major < _FIRST_YEAR_MAJOR:
        raise ConversionGap(
            f"The VI was saved in LabVIEW {version}, whose version number is not "
            "a year (the major version number is the release year minus 2000 "
            "from LabVIEW 2009, 9.0, on)",
            node,
        )
    return _YEAR_BASE + major


def fill(
    value: DefaultObjectProperty,
    expression: str,
    receiver: str,
    ctx: CodeGenContext,
    node: PrimitiveNode,
) -> str:
    """``expression`` (``value.python``) with ``{application}`` as ``receiver`` and
    the VI's saved LabVIEW version and year written in; imports the names of
    ``value.panels`` that the finished expression uses."""
    expression = expression.replace("{application}", receiver)
    if "{lv_version}" in expression or "{lv_year}" in expression:
        version = _saved_version(ctx, node)
        expression = expression.replace("{lv_version}", repr(version))
        if "{lv_year}" in expression:
            expression = expression.replace(
                "{lv_year}", str(_saved_year(version, node))
            )
    used = {
        part.id
        for part in ast.walk(parse_expr(expression))
        if isinstance(part, ast.Name)
    }
    names = [name for name in value.panels if name in used]
    if names:
        ctx.import_panels(", ".join(names))
    return expression
