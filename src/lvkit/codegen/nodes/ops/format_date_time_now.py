"""Format Date/Time String: an unwired ``time stamp`` is the current date and time
(NI, "Format Date/Time String Function", labview-api-ref,
``functions/format-date-time-string.html``: "The default is the current date and
time"), written as Get Date/Time In Seconds writes it: now, in UTC."""

from __future__ import annotations

from lvkit.terminal_default import DefaultBehavior

from . import register_op_behaviors

register_op_behaviors(
    "prim:8100", {DefaultBehavior.CURRENT_TIME: "hightime.datetime.now(UTC)"}
)
