"""Private-data fields whose labels become one Python name each get their own
attribute in the generated ``__init__``."""

from __future__ import annotations

import ast
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit.codegen.class_builder.init_builder import _InitBuilderMixin
from lvkit.structure import LVClass, LVMethod, LVPrivateDataField, _resolve_type_ids


def _attributes(labels: tuple[str, ...]) -> tuple[list[str], list[str]]:
    descs = "".join(f'<TypeDesc Type="String" Label="{lb}"/>' for lb in labels)
    root = ET.fromstring(f"<R><VCTP><Section>{descs}</Section></VCTP></R>")
    fields = _resolve_type_ids(root, [str(i) for i in range(len(labels))])
    cls = LVClass(name="C", path=Path("C.lvclass"), private_data_fields=fields)
    func = _InitBuilderMixin()._build_init(cls, False)
    attrs = [
        s.targets[0].attr  # type: ignore[attr-defined]
        for s in func.body
        if isinstance(s, ast.Assign)
    ]
    return [f.name for f in fields], attrs


@pytest.mark.parametrize(
    ("labels", "labelled", "attributes"),
    [
        (("", "Field 0"), ["field_0", "Field 0"], ["_field_0", "_field_0_2"]),
        (("Count", "count"), ["Count", "count"], ["_count", "_count_2"]),
        (("a b", "a_b"), ["a b", "a_b"], ["_a_b", "_a_b_2"]),
        (("x", "y"), ["x", "y"], ["_x", "_y"]),
    ],
)
def test_each_field_gets_its_own_attribute(
    labels: tuple[str, ...], labelled: list[str], attributes: list[str]
) -> None:
    names, attrs = _attributes(labels)
    assert names == labelled
    assert attrs == attributes


def test_fields_are_private_whatever_the_scope_of_a_property_over_them() -> None:
    cls = LVClass(
        name="C",
        path=Path("C.lvclass"),
        private_data_fields=[
            LVPrivateDataField(name="Count", python_type="int"),
            LVPrivateDataField(name="count", python_type="int"),
        ],
        methods=[
            LVMethod(
                name="Read count",
                vi_path="Read count.vi",
                scope="public",
                property_name="count",
            )
        ],
    )
    func = _InitBuilderMixin()._build_init(cls, False)
    attrs = [
        s.targets[0].attr  # type: ignore[attr-defined]
        for s in func.body
        if isinstance(s, ast.Assign)
    ]
    assert attrs == ["_count", "_count_2"]
