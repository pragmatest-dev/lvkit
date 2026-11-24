"""A class object in a variant, at run time: the variants module writes and reads it
through the package's ``classes`` module, the one dict of the classes of the package
(``codegen/classes_module.py``). The class is imported only when data holds it, and
a name the dict lacks is LabVIEW error 1527 (NI, "LabVIEW Object-Oriented
Programming Error Codes": "a LabVIEW class that is not currently loaded")."""

from __future__ import annotations

import importlib
import struct
import sys
from pathlib import Path

import pytest

from lvkit.type_string import decode, encode
from tests.test_flat_class import BASE, CHILD, classes, pascal_name, round_trip


def _variant_type(variants, lv_type):
    """The ``LVType`` of the package's ``variants`` module for the class type."""
    numbers = tuple(encode(lv_type))
    return variants.LVType(0x70, "Refnum", numbers)


def test_a_class_object_round_trips_through_a_variant(tmp_path: Path) -> None:
    project = classes(tmp_path)
    project.add_vi("RoundTrip", round_trip(BASE))
    package = project.generate()
    with package.use() as module:
        variants = module("variants")
        lv_type = _variant_type(variants, BASE)
        obj = module("child").Child()
        obj._Base__count, obj._Base__name, obj._Child__extra = 3, "x", 9
        numbers, data = variants.variant_to_flattened(variants.Variant(obj, lv_type))
        assert numbers == list(lv_type.type_string)
        base_part = struct.pack(">i", 3) + struct.pack(">i", 1) + b"x"
        assert data.encode("latin-1").startswith(
            struct.pack(">I", 2) + pascal_name("Child.lvclass")
        )
        assert data.encode("latin-1").endswith(
            struct.pack(">i", len(base_part))
            + base_part
            + struct.pack(">i", 4)
            + struct.pack(">i", 9)
        )
        back = variants.variant_from_flattened(data, numbers)
        assert type(back.value) is module("child").Child
        assert (
            back.value._Base__count,
            back.value._Base__name,
            back.value._Child__extra,
        ) == (3, "x", 9)


def test_a_class_of_the_dict_is_imported_only_when_data_holds_it(
    tmp_path: Path,
) -> None:
    project = classes(tmp_path)
    project.add_vi("RoundTrip", round_trip(BASE))
    package = project.generate()
    with package.use() as module:
        variants = module("variants")
        lv_type = _variant_type(variants, BASE)
        assert "pkg.child" not in sys.modules
        # Two levels at version 1, each cluster the zero size of the default data.
        default = (
            struct.pack(">I", 2)
            + pascal_name("Child.lvclass")
            + struct.pack(">HHHH", 1, 0, 0, 0) * 2
            + struct.pack(">i", 0) * 2
        )
        back = variants.variant_from_flattened(
            default.decode("latin-1"), list(lv_type.type_string)
        )
        assert "pkg.child" in sys.modules
        assert type(back.value) is module("child").Child
        assert importlib.import_module("pkg.lv_classes").CLASSES == {
            "Base.lvclass": ("base", "Base"),
            "Child.lvclass": ("child", "Child"),
        }


def test_a_class_name_the_dict_lacks_is_error_1527(tmp_path: Path) -> None:
    project = classes(tmp_path)
    project.add_vi("RoundTrip", round_trip(BASE))
    package = project.generate()
    with package.use() as module:
        variants = module("variants")
        lv_type = _variant_type(variants, BASE)
        unknown = struct.pack(">I", 1) + pascal_name("Other.lvclass") + b"\x00" * 8
        with pytest.raises(Exception) as read:
            variants.variant_from_flattened(
                unknown.decode("latin-1"), list(lv_type.type_string)
            )
        assert read.value.code == 1527
        assert "Other.lvclass" in str(read.value)


def test_the_type_string_of_a_class_names_it() -> None:
    assert decode(encode(CHILD)).classname == "Child.lvclass"
