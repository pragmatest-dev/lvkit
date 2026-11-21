"""A class type whose file could not be tied while a cycle of files was loading is
tied once the load has finished (``graph/retie_types.py``)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from lvkit.graph.retie_types import retie_unresolved_types
from lvkit.graph.type_records import NamedRecord, TypeRecords
from lvkit.models import LVType, LVTypeKind

CLASS_NAME = "Lib.lvlib:Thing.lvclass"


def _class_type() -> LVType:
    return LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Refnum",
        ref_type="UDClassInst",
        classname=CLASS_NAME,
    )


class _Graph:
    """A VI ``vi`` whose records, at the first tie, held no record of the class;
    the control ``ctl`` it is nested in records it once its load is done."""

    def __init__(self, terminal_type: LVType) -> None:
        self.records: dict[str, TypeRecords] = {
            "vi": TypeRecords(
                owners=("Lib.lvlib",),
                records=(),
                nested=("ctl",),
                resolved={CLASS_NAME.casefold(): None},
            ),
            "ctl": TypeRecords(
                owners=("Lib.lvlib",),
                records=(NamedRecord("Thing.lvclass", 3, "thing.lvclass", True),),
                nested=(),
            ),
        }
        node = SimpleNamespace(
            lv_type=None, terminals=[SimpleNamespace(lv_type=terminal_type)]
        )
        self.nodes = [node]

        self._untied_type_vis: set[str] = {"vi"}

    def type_records_of(self, key: str) -> TypeRecords | None:
        return self.records.get(key)

    def iter_nodes(self, key: str) -> list[Any]:
        return self.nodes

    def recorded_type_key(self, file_key: str, lv_type: LVType) -> str | None:
        from lvkit.graph.type_records import resolve_type

        return resolve_type(file_key, lv_type, self.type_records_of)

    def is_strict_typedef(self, lv_type: LVType) -> bool:
        return False


def test_a_class_type_left_without_a_file_is_tied_after_the_load() -> None:
    lv_type = _class_type()
    graph = _Graph(lv_type)
    retie_unresolved_types(graph)  # type: ignore[arg-type]
    assert lv_type.file_key == "thing.lvclass"


def test_a_vi_whose_tie_did_not_miss_is_not_walked() -> None:
    lv_type = _class_type()
    graph = _Graph(lv_type)
    graph._untied_type_vis.clear()
    retie_unresolved_types(graph)  # type: ignore[arg-type]
    assert lv_type.file_key is None


def test_the_recorded_misses_are_cleared_after_the_retie() -> None:
    graph = _Graph(_class_type())
    retie_unresolved_types(graph)  # type: ignore[arg-type]
    assert graph._untied_type_vis == set()


def test_a_type_already_tied_is_not_touched() -> None:
    lv_type = _class_type()
    lv_type.file_key = "elsewhere.lvclass"
    graph = _Graph(lv_type)
    retie_unresolved_types(graph)  # type: ignore[arg-type]
    assert lv_type.file_key == "elsewhere.lvclass"


def test_a_class_no_record_names_stays_without_a_file() -> None:
    lv_type = _class_type()
    graph = _Graph(lv_type)
    graph.records["ctl"] = TypeRecords(owners=(), records=(), nested=())
    retie_unresolved_types(graph)  # type: ignore[arg-type]
    assert lv_type.file_key is None
