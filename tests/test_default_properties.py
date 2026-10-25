"""A Property Node with an unwired reference reads the documented properties of
its class's default object; every other property stays unavailable."""

from __future__ import annotations

import ast
import getpass

import pytest

from lvkit.codegen.ast_utils import to_var_name
from lvkit.codegen.nodes import property_node
from lvkit.models import PropertyDef, PropertySegment
from lvkit.runtime import lv
from lvkit.runtime.default_properties import DEFAULT_PROPERTIES
from tests.test_property_terminal_order import fixture


def _read_unwired_app_property(ni_name: str):
    """Generate the unwired App Property Node that reads ``ni_name`` and run it."""
    node, ctx, namespace = fixture(
        [("Value", "output")], ref_value=None, class_name="App"
    )
    node.properties = [PropertyDef(segments=[PropertySegment(name=ni_name, code=1)])]
    fragment = property_node.generate(node, ctx)
    code = ast.fix_missing_locations(
        ast.Module(body=fragment.statements, type_ignores=[])
    )
    exec(compile(code, "property-test", "exec"), namespace)  # noqa: S102
    return namespace[fragment.bindings["row_0"]]


def test_unwired_app_user_name_is_the_account_running_the_process():
    assert _read_unwired_app_property("App.UserName") == getpass.getuser()


def test_other_app_properties_stay_unavailable():
    with pytest.raises(lv.UnavailableReferenceMember, match="App.*'appkind'"):
        _read_unwired_app_property("App.Kind")


def test_a_written_property_wins_over_the_documented_value():
    reference = lv.default_reference("App", "Written.vi")
    setattr(reference, "appusername", "someone")
    assert reference.appusername == "someone"


def test_every_documented_attribute_is_the_name_generated_code_reads():
    for properties in DEFAULT_PROPERTIES.values():
        for prop in properties.values():
            assert to_var_name(prop.ni_name) == prop.attribute
