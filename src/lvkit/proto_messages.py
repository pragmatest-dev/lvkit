"""The protobuf messages a LabVIEW gRPC program names, and the Python class of each.

``data/grpc/proto_messages.json`` lists, by the name LabVIEW's generated code gives
the message (the underscored proto name, for example
``ni_measurementlink_sessionmanagement_v1_UnreserveSessionsRequest``), the proto
message's full name, the PyPI distribution that ships its generated Python, the
module that holds it and its attribute path in that module. The table is written
by ``scripts/build_proto_messages.py`` from the packages' own ``_pb2`` modules. A
message not listed has no known Python class.
"""

from __future__ import annotations

import json
from functools import lru_cache

from pydantic import BaseModel

from ._data import data_dir


class ProtoMessage(BaseModel):
    """One proto message: ``proto_name`` is its full name, ``module`` the Python
    module of its generated code (shipped by PyPI ``distribution``) and
    ``message`` its attribute path in that module."""

    proto_name: str
    distribution: str
    module: str
    message: str

    def import_statement(self) -> str:
        """The import of ``module`` by its full name: two packages can each
        have a module of the same last name (``measurement_service_pb2``)."""
        return f"import {self.module}"

    def expression(self) -> str:
        """The Python expression of the message class."""
        return f"{self.module}.{self.message}"


@lru_cache(maxsize=1)
def _load() -> dict[str, ProtoMessage]:
    path = data_dir() / "grpc" / "proto_messages.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        name: ProtoMessage.model_validate(row) for name, row in raw["messages"].items()
    }


def find_proto_message(name: str) -> ProtoMessage | None:
    """The message registered under ``name``; None when the table does not list
    it."""
    return _load().get(name)
