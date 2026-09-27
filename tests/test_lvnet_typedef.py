"""lvnet text for a ``.ctl`` typedef: ``typedef <Name> :`` with ``uses``, ``type``,
``fields`` (recorded defaults) and, verbose, the ``types :`` footnote."""

from __future__ import annotations

import asyncio

import pytest

from lvkit.graph.describe_typedef import describe_ctl_file
from lvkit.graph.lvnet_typedef import render_lvnet_typedef
from lvkit.graph.node_kinds import NodeType
from lvkit.graph.typedef import TypedefField, TypedefInfo, TypedefRef
from lvkit.mcp import server as srv
from lvkit.models import ClusterField, EnumValue, LVType, LVTypeKind

from .conftest import SAMPLES_ROOT

_ICON_API = SAMPLES_ROOT / "ni-labview-icon-editor/vi.lib/LabVIEW Icon API"
_CLUSTER_CTL = _ICON_API / "API_Test Settings.ctl"

_STRING = LVType(LVTypeKind.PRIMITIVE, underlying_type="String")
_DBL = LVType(LVTypeKind.PRIMITIVE, underlying_type="DBL")


def _info(**kw) -> TypedefInfo:
    base = {
        "key": "/p/dir/Cfg.ctl",
        "name": "Cfg.ctl",
        "root_type": LVType(LVTypeKind.CLUSTER),
        "default": None,
        "fields": (),
        "uses": (),
        "owned_by": (),
        "used_by": (),
    }
    return TypedefInfo(**{**base, **kw})


def test_a_cluster_control_renders_type_and_fields_with_defaults() -> None:
    inner = LVType(LVTypeKind.CLUSTER, fields=[ClusterField("R", _DBL)])
    root = LVType(
        LVTypeKind.CLUSTER,
        fields=[ClusterField("Name", _STRING), ClusterField("Color", inner)],
    )
    fields = (
        TypedefField("Name", _STRING, "abc", ()),
        TypedefField("Gain", _DBL, "3600.0", ()),
        TypedefField("Steps", LVType(LVTypeKind.ARRAY, element_type=_DBL), None, (),
                     ("1.0", "2.0")),
        TypedefField("Color", inner, None, (TypedefField("R", _DBL, "0.5", ()),)),
    )
    lines = render_lvnet_typedef(_info(root_type=root, fields=fields)).splitlines()
    assert lines[0] == "typedef Cfg.ctl :"
    assert "  type : Cluster{ Name : String, Color : Cluster{ R : DBL } }" in lines
    assert "  fields :" in lines
    assert '    Name : String default "abc"' in lines  # a string default is quoted
    assert "    Gain : DBL default 3600.0" in lines
    assert "    Steps : [DBL] default [1.0, 2.0]" in lines
    assert "      R : DBL default 0.5" in lines  # nested one level in


def test_an_enum_control_has_a_type_and_a_default_but_no_fields() -> None:
    enum = LVType(LVTypeKind.ENUM, values={"Read": EnumValue(0), "Write": EnumValue(1)})
    text = render_lvnet_typedef(_info(root_type=enum, default="Write"))
    assert "  type : Enum{ Read = 0, Write = 1 }" in text
    assert "  default Write" in text and "fields :" not in text


def test_uses_are_paths_relative_to_the_control() -> None:
    uses = (
        TypedefRef(NodeType.TYPEDEF, "Dep.ctl", "/p/dir/sub/Dep.ctl"),
        TypedefRef(NodeType.CLASS, "Cls.lvclass", None),  # a stub: no path
    )
    lines = render_lvnet_typedef(_info(uses=uses)).splitlines()
    assert lines[1] == "  uses :"
    assert lines[2].split() == ["typedef", "Dep.ctl", ";", "./sub/Dep.ctl"]
    assert lines[3].split() == ["class", "Cls.lvclass"]  # a stub: no path


def test_a_control_cannot_use_a_library() -> None:
    lib = TypedefRef(NodeType.LIBRARY, "L.lvlib", "/p/L.lvlib")
    with pytest.raises(ValueError, match="Cfg.ctl: a library is not a dependency kind"):
        render_lvnet_typedef(_info(uses=(lib,)))


def test_names_and_string_defaults_are_escaped_like_a_vi_terminal() -> None:
    fields = (
        TypedefField("Slope ", _DBL, "1.0", ()),  # a trailing space forces quoting
        TypedefField("Note", _STRING, 'say "hi"\r\nnow', ()),
        TypedefField("Names", LVType(LVTypeKind.ARRAY, element_type=_STRING), None, (),
                     ("a", 'b"c')),
    )
    lines = render_lvnet_typedef(_info(fields=fields)).splitlines()
    assert '    "Slope " : DBL default 1.0' in lines
    assert '    Note : String default "say \\"hi\\"\\r\\nnow"' in lines
    assert len(lines) == 6  # a CRLF in a default never adds a physical line
    assert '    Names : [String] default ["a", "b\\"c"]' in lines


def test_the_footnote_covers_a_named_type_under_an_array_root() -> None:
    named = LVType(
        LVTypeKind.CLUSTER, typedef_name="Row.ctl", typedef_path="Row.ctl",
        fields=[ClusterField("A", _DBL)],
    )
    root = LVType(LVTypeKind.ARRAY, element_type=named, dimensions=1)
    text = render_lvnet_typedef(_info(root_type=root), verbose=True)
    assert "  type : [Row]" in text
    assert "    Row = Cluster{ A : DBL } ; ./Row.ctl" in text


def test_the_control_itself_is_not_in_its_own_footnote() -> None:
    own = LVType(
        LVTypeKind.CLUSTER, typedef_name="Cfg.ctl", fields=[ClusterField("A", _DBL)]
    )
    text = render_lvnet_typedef(_info(root_type=own), verbose=True)
    assert "types :" not in text  # nothing else named is reached


def test_read_tools_reject_an_unknown_format() -> None:
    for tool, arg in ((srv.read_ctl, "x.ctl"), (srv.read_vi, "x.vi")):
        with pytest.raises(ValueError, match="Unknown format"):
            asyncio.run(tool(arg, format="text"))


def test_the_types_footnote_is_verbose_only_and_lists_named_types() -> None:
    named = LVType(
        LVTypeKind.CLUSTER,
        typedef_name="Inner.ctl",
        typedef_path="Inner.ctl",
        fields=[ClusterField("A", _DBL)],
    )
    fields = (TypedefField("In", named, None, ()),)
    root = LVType(LVTypeKind.CLUSTER, fields=[ClusterField("In", named)])
    info = _info(root_type=root, fields=fields)
    assert "types :" not in render_lvnet_typedef(info)
    verbose = render_lvnet_typedef(info, verbose=True)
    assert "  types :\n    Inner = Cluster{ A : DBL } ; ./Inner.ctl" in verbose


@pytest.mark.needs_samples
@pytest.mark.skipif(not _CLUSTER_CTL.exists(), reason="icon-editor sample absent")
def test_a_real_control_through_describe_and_mcp():
    text = describe_ctl_file(_CLUSTER_CTL, fmt="lvnet")
    assert isinstance(text, str) and text.startswith("typedef API_Test Settings.ctl :")
    assert "    Font : String default" in text and "Text color" in text
    result = asyncio.run(srv.read_ctl(str(_CLUSTER_CTL), format="lvnet"))
    assert result == {"lvnet": text}
    verbose = describe_ctl_file(_CLUSTER_CTL, fmt="lvnet", verbose=True)
    assert isinstance(verbose, str) and "  types :" in verbose
    assert "    API_Text Colors = Cluster{" in verbose  # the named type, lossless


def test_enum_items_and_numbers_are_spelled_like_lvnet_words() -> None:
    enum = LVType(LVTypeKind.ENUM, values={"Read, Write": EnumValue(0)})
    fields = (
        TypedefField("Mode", enum, "Read, Write", ()),  # an unsafe word: quoted
        TypedefField("Plain", enum, "Stop", ()),  # a safe word: bare
        TypedefField("Gain", _DBL, "-1.5e-06", ()),  # a number: bare
    )
    lines = render_lvnet_typedef(_info(fields=fields)).splitlines()
    assert '    Mode : Enum{ "Read, Write" = 0 } default "Read, Write"' in lines
    assert "    Plain : Enum{ \"Read, Write\" = 0 } default Stop" in lines
    assert "    Gain : DBL default -1.5e-06" in lines


def test_an_equal_copy_of_the_controls_own_type_is_not_footnoted() -> None:
    def own() -> LVType:
        return LVType(
            LVTypeKind.CLUSTER, typedef_name="Cfg.ctl", fields=[ClusterField("A", _DBL)]
        )

    root = own()
    root.fields = [ClusterField("Self", own())]  # a distinct object, same name
    text = render_lvnet_typedef(_info(root_type=root), verbose=True)
    assert "types :" not in text


def test_a_root_array_control_carries_its_saved_elements() -> None:
    root = LVType(LVTypeKind.ARRAY, element_type=_DBL, dimensions=1)
    info = _info(root_type=root, elements=("1.0", "2.0"))
    assert "  default [1.0, 2.0]" in render_lvnet_typedef(info)
