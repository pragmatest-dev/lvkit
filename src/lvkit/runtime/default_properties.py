"""Property values of the default object of a VI Server class.

A Property Node whose reference input is unwired acts on the default object of
its class (see ``lv.DefaultReference``). A property is listed here only when
NI's public documentation says what its value is and that value is available to
a Python process; every other property of the default object stays unavailable.

Each entry's ``attribute`` is the attribute name generated code reads: the
property's recorded name (``App.UserName``) with the characters that are not
valid in an identifier removed and lower-cased, as ``codegen`` names it.
"""

from __future__ import annotations

import getpass
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DefaultProperty:
    """One documented property of a class's default object."""

    attribute: str
    ni_name: str
    read: Callable[[], Any]


# NI, Application:User Name (docs-be.ni.com/api/bundle/labview-api-ref/page/
# properties-and-methods/vi-server/application/username.html): "User name used
# to open the application." The application here is the running Python
# process, so the user that opened it is the account the process runs as.
_APPLICATION_PROPERTIES = (
    DefaultProperty("appusername", "App.UserName", getpass.getuser),
)

DEFAULT_PROPERTIES: dict[str, dict[str, DefaultProperty]] = {
    "App": {prop.attribute: prop for prop in _APPLICATION_PROPERTIES},
}
