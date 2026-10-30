"""Class private-data fields: a label is kept as written; no label names the
field by its position in the cluster."""

from __future__ import annotations

import ast
import xml.etree.ElementTree as ET
from pathlib import Path

from lvkit.codegen.class_builder.init_builder import _InitBuilderMixin
from lvkit.structure import LVClass, LVPrivateDataField, _resolve_type_ids


def _vctp(*labels: str) -> ET.Element:
    descs = "".join(f'<TypeDesc Type="String" Label="{label}"/>' for label in labels)
    return ET.fromstring(f"<R><VCTP><Section>{descs}</Section></VCTP></R>")


def test_label_none_is_a_field_and_unlabelled_gets_position_name() -> None:
    root = _vctp("None", "", "Count")
    fields = _resolve_type_ids(root, ["0", "1", "2"])
    assert [f.name for f in fields] == ["None", "field_1", "Count"]


def test_init_keeps_every_field() -> None:
    cls = LVClass(
        name="C",
        path=Path("C.lvclass"),
        private_data_fields=[
            LVPrivateDataField(name="None", python_type="str"),
            LVPrivateDataField(name="field_1", python_type="int"),
        ],
    )
    func = _InitBuilderMixin()._build_init(cls, None)
    targets = [
        stmt.targets[0].attr  # type: ignore[attr-defined]
        for stmt in func.body
        if isinstance(stmt, ast.Assign)
    ]
    assert targets == ["none", "field_1"]
