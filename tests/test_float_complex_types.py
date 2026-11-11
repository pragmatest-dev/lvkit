"""Extended-precision floats (EXT) and complex numbers (CSG, CDB, CXT), with or
without a unit, are ``float`` and ``complex`` in generated code: in the signature,
in the result, and as the default an unwired indicator takes."""

from __future__ import annotations

import pytest

from lvkit.codegen.nodes.ops._numeric import is_float, is_real
from lvkit.formula.emit import VarSpec, transpile
from lvkit.graph.lvnet_scalar import lvnet_typed_scalar
from lvkit.graph.netlist_build import _default_literal
from lvkit.graph.unwired_default import default_fits
from lvkit.lv_type_codes import COMPLEX_TYPE_NAMES, REAL_FLOAT_TYPE_NAMES
from lvkit.models import LVType, LVTypeKind
from lvkit.render.style import type_family
from tests.error_diagrams import (
    REQUIRED,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)

NAME = "Ext Complex.vi"


@pytest.mark.parametrize("name", sorted(REAL_FLOAT_TYPE_NAMES))
def test_every_real_float_type_is_a_python_float(name: str) -> None:
    lv_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=name)
    assert lv_type.to_python() == "float"


@pytest.mark.parametrize("name", sorted(COMPLEX_TYPE_NAMES))
def test_every_complex_type_is_a_python_complex(name: str) -> None:
    lv_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=name)
    assert lv_type.to_python() == "complex"


def _numbers(project: Project):  # noqa: F811
    vi = Vi(NAME)
    ext = vi.add_type('<TypeDesc Type="NumFloatExt" />')
    csg = vi.add_type('<TypeDesc Type="NumComplex64" />')
    ext_array = vi.add_type(
        f'<TypeDesc Type="Array"><Dimension /><TypeDesc TypeID="{ext}" /></TypeDesc>'
    )
    vi.wire(vi.control("x", ext, REQUIRED), vi.indicator("x out", ext))
    vi.wire(vi.control("z", csg, REQUIRED), vi.indicator("z out", csg))
    vi.wire(vi.control("xs", ext_array, REQUIRED), vi.indicator("xs out", ext_array))
    vi.indicator("unwired x", ext)
    vi.indicator("unwired z", csg)
    project.add(vi)
    return project.generate(NAME)


def test_ext_and_complex_controls_are_typed_and_pass_through(
    project: Project,  # noqa: F811
) -> None:
    package = _numbers(project)
    assert "x: float" in package.source
    assert "z: complex" in package.source
    assert "xs: list[float]" in package.source
    assert "Any" not in package.source
    result = package.function(1.25, 2 + 3j, [0.5, 1.5])
    assert result.x_out == 1.25
    assert result.z_out == 2 + 3j
    assert result.xs_out == [0.5, 1.5]


def test_an_unwired_ext_or_complex_indicator_is_zero_of_its_type(
    project: Project,  # noqa: F811
) -> None:
    result = _numbers(project).function(0.0, 0j, [])
    assert result.unwired_x == 0.0
    assert isinstance(result.unwired_x, float)
    assert result.unwired_z == 0j
    assert isinstance(result.unwired_z, complex)


def _prim(name: str) -> LVType:
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=name)


def test_a_unit_float_wire_has_the_float_family_color() -> None:
    assert type_family(_prim("UnitFloat64")) == type_family(_prim("NumFloat64"))
    assert type_family(_prim("UnitFloat64")) == "float"
    assert type_family(_prim("UnitComplex128")) == "float"


def test_a_unit_float_default_reads_back_as_a_float() -> None:
    assert lvnet_typed_scalar("0.5", _prim("UnitFloatExt")) == 0.5
    assert isinstance(lvnet_typed_scalar("0.5", _prim("UnitFloat64")), float)


def test_a_documented_float_fits_a_unit_float_terminal() -> None:
    assert default_fits(0.5, _prim("UnitFloat64")) is default_fits(
        0.5, _prim("NumFloat64")
    )
    assert default_fits(0.5, _prim("UnitFloat64")) is True


def test_an_unwired_unit_float_tunnel_shows_float_and_complex_zero() -> None:
    assert _default_literal(_prim("UnitFloat64")) == _default_literal(
        _prim("NumFloat64")
    )
    assert _default_literal(_prim("UnitFloat64")) == "0.0"
    assert _default_literal(_prim("UnitComplex128")) == "0+0i"


def test_a_unit_float_is_float_and_real_to_the_numeric_ops() -> None:
    assert is_float(_prim("UnitFloat64")) is is_float(_prim("NumFloat64")) is True
    assert is_real(_prim("UnitFloatExt")) is is_real(_prim("NumFloatExt")) is True


def test_a_formula_variable_of_unit_float_type_transpiles_like_its_num_twin() -> None:
    def source(lv_type: str) -> str:
        variables = [
            VarSpec("x", lv_type, "in", False),
            VarSpec("y", lv_type, "out", False),
        ]
        return transpile("y = x / 2;", variables).source

    assert source("UnitFloat64") == source("NumFloat64")
