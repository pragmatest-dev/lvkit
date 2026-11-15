"""Write ``src/lvkit/data/grpc/proto_messages.json``: every protobuf message of NI's
ni-apis-python packages (and protobuf's own well-known types), read from the
``_pb2`` modules' descriptors.

Run it with the interpreter of an environment where those packages are
installed, for example::

    uv venv .tmp/protos_venv
    uv pip install --python .tmp/protos_venv/bin/python \\
        ni.measurementlink.measurement.v1.proto \\
        ni.measurementlink.measurement.v2.proto \\
        ni.measurementlink.sessionmanagement.v1.proto \\
        ni.measurementlink.discovery.v1.proto ni.measurementlink.pinmap.v1.proto \\
        ni.protobuf.types ni.grpcdevice.v1.proto
    .tmp/protos_venv/bin/python scripts/build_proto_messages.py

Each message is keyed by the name LabVIEW's generated code gives it (its proto
full name with ``.`` as ``_``). A map entry has no class a program can name, so
it is not listed.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import sys
from pathlib import Path

from google.protobuf.descriptor import Descriptor

DISTRIBUTIONS = (
    "ni.measurementlink.measurement.v1.proto",
    "ni.measurementlink.measurement.v2.proto",
    "ni.measurementlink.sessionmanagement.v1.proto",
    "ni.measurementlink.discovery.v1.proto",
    "ni.measurementlink.pinmap.v1.proto",
    "ni.measurementlink.proto",
    "ni.grpcdevice.v1.proto",
    "ni.protobuf.types",
    "protobuf",
)
SOURCE = (
    "message classes of NI's ni-apis-python packages and protobuf's well-known "
    "types, listed from their generated _pb2 modules by "
    "scripts/build_proto_messages.py"
)
OUTPUT = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "lvkit"
    / "data"
    / "grpc"
    / "proto_messages.json"
)


def _modules(distribution: str) -> list[str]:
    """The ``_pb2`` modules the distribution installs, sorted."""
    found: set[str] = set()
    for file in importlib.metadata.files(distribution) or []:
        parts = file.parts
        if parts[0] == ".." or "compiler" in parts:
            continue
        if file.name == "__init__.py" and parts[-2].endswith("_pb2"):
            found.add(".".join(parts[:-1]))
        elif file.name.endswith("_pb2.py"):
            found.add(".".join((*parts[:-1], file.stem)))
    return sorted(found)


def _messages(descriptor: Descriptor, path: str) -> list[tuple[str, str]]:
    rows = [(descriptor.full_name, path)]
    for nested in descriptor.nested_types:
        if nested.GetOptions().map_entry:
            continue
        rows.extend(_messages(nested, f"{path}.{nested.name}"))
    return rows


def build() -> dict[str, dict[str, str]]:
    messages: dict[str, dict[str, str]] = {}
    for distribution in DISTRIBUTIONS:
        name = importlib.metadata.metadata(distribution)["Name"]
        for module_name in _modules(distribution):
            module = importlib.import_module(module_name)
            descriptors = module.DESCRIPTOR.message_types_by_name
            for top in descriptors.values():
                for full_name, path in _messages(top, top.name):
                    key = full_name.replace(".", "_")
                    if key in messages:
                        continue
                    messages[key] = {
                        "proto_name": full_name,
                        "distribution": name,
                        "module": module_name,
                        "message": path,
                    }
    return dict(sorted(messages.items()))


def main() -> None:
    table = {"source": SOURCE, "messages": build()}
    OUTPUT.write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")
    print(f"{len(table['messages'])} messages -> {OUTPUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
