"""The module a generated package holds for LabVIEW user events.

A user event is the ``list`` of the queues of the registrations that hold it
(``nodes/user_events.py``). A cluster or class that holds a user event is copied by
value, and LabVIEW copies a refnum as the same refnum, so the package gets a
``UserEvent`` class: a ``list`` whose deep copy is itself. It is written once into
the package root as ``user_events.py``, only when a generated module imports it, and
imported from there, so a generated package imports nothing from lvkit.
"""

from __future__ import annotations

USER_EVENTS_MODULE_NAME = "user_events"
USER_EVENT_CLASS = "UserEvent"

USER_EVENTS_MODULE_SOURCE = '''"""LabVIEW user events."""

from __future__ import annotations

from typing import Any


class UserEvent(list):
    """A user event: the queues of the registrations that hold it. It is a
    reference: a class or cluster that holds it is copied by value, and the copy
    refers to this same user event."""

    def __deepcopy__(self, memo: dict[int, Any]) -> UserEvent:
        return self
'''
