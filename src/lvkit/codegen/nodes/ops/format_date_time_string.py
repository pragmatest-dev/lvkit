"""Format Date/Time String (prim 8100).

in_1 = UTC format, in_2 = time stamp, in_3 = time format string -> out 0 = the
date/time string. NI, "Format Date/Time String Function" (labview-api-ref,
``functions/format-date-time-string.html``): "UTC format specifies if the output
string is in Universal Time or in the configured time zone for the computer"; a
time stamp is UTC, so local time is the computer's offset from UTC as
``local_offset`` of the package's ``local_time`` module
(``codegen/labview_local_time.py``) gives it. The
codes are written by ``_lv_format_time`` (``codegen/labview_time_format.py``),
whose source is copied into the module.
"""

from __future__ import annotations

import ast
import inspect
from typing import TYPE_CHECKING

from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive

from ...errors import CodeGenError
from ...labview_time_format import _lv_format_time
from ...local_time_module import LOCAL_OFFSET_NAME
from . import register_context_op

if TYPE_CHECKING:
    from ...context import CodeGenContext

_LOCAL = f"in_2.astimezone(datetime.timezone({LOCAL_OFFSET_NAME}(in_2)))"


@register_context_op("FORMAT_DATE_TIME_STRING")
def format_date_time_string(
    node: PrimitiveNode, resolved: ResolvedPrimitive, ctx: CodeGenContext
) -> dict[str, str]:
    (definition,) = ast.parse(inspect.getsource(_lv_format_time)).body
    assert isinstance(definition, ast.FunctionDef)
    name = ctx.add_module_function(definition)
    if name != definition.name:
        raise CodeGenError(f"The module-level name {definition.name} is taken")
    ctx.import_local_time(LOCAL_OFFSET_NAME)
    return {"datetime_string": f"{name}(in_2 if in_1 else {_LOCAL}, in_3)"}
