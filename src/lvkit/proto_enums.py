"""The protobuf enums a LabVIEW gRPC program names, and the Python class of each.

``data/grpc/proto_enums.json`` lists, by the name the generated LabVIEW code
registers the enum under (the underscored proto name, for example
``google_protobuf_Field_Kind``), the proto enum's full name, the PyPI
distribution that ships its generated Python, the module that holds it and its
attribute path in that module. An enum not listed has no known Python class.
"""

from __future__ import annotations

import json
from functools import lru_cache

from pydantic import BaseModel

from ._data import data_dir


class ProtoEnum(BaseModel):
    """One proto enum: ``proto_name`` is its full name, ``module`` the Python
    module of its generated code (shipped by PyPI ``distribution``) and
    ``enum`` its attribute path in that module."""

    proto_name: str
    distribution: str
    module: str
    enum: str

    def import_statement(self) -> str:
        """The import of ``module``, which the enum is read from by ``expression``."""
        package, _, leaf = self.module.rpartition(".")
        return f"from {package} import {leaf}" if package else f"import {leaf}"

    def expression(self) -> str:
        """The Python expression of the enum class."""
        return f"{self.module.rpartition('.')[2]}.{self.enum}"


@lru_cache(maxsize=1)
def _load() -> dict[str, ProtoEnum]:
    path = data_dir() / "grpc" / "proto_enums.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {name: ProtoEnum.model_validate(row) for name, row in raw["enums"].items()}


def find_proto_enum(name: str) -> ProtoEnum | None:
    """The enum registered under ``name``; None when the table does not list it."""
    return _load().get(name)
