"""A class's private-data cluster is identified by its owner labels: the class
and the control the class file records, never by the cluster's own label."""

from pathlib import Path

from lvkit.structure import _fields_from_xml


def _typedef(labels: list[str], cluster_label: str, ids: str = "0,1") -> str:
    lbls = "".join(f'<Label Text="{t}"/>' for t in labels)
    kids = "".join(f'<TypeDesc TypeID="{i}"/>' for i in ids.split(","))
    return (
        f'<TypeDesc Type="TypeDef">{lbls}'
        f'<TypeDesc Type="Cluster" Nested="True" Label="{cluster_label}">'
        f"{kids}</TypeDesc></TypeDesc>"
    )


def _write(tmp_path: Path, *typedefs: str) -> Path:
    xml = (
        '<?xml version="1.0"?><Root><VCTP><Section>'
        '<TypeDesc Type="Numeric" Label="alpha"/>'
        '<TypeDesc Type="String" Label="beta"/>'
        + "".join(typedefs)
        + "</Section></VCTP></Root>"
    )
    path = tmp_path / "vi.xml"
    path.write_text(xml)
    return path


def _names(fields) -> list[str] | None:
    return None if fields is None else [f.name for f in fields]


def test_relabelled_cluster_resolves_by_recorded_control(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _typedef(["My Class.lvclass", "My Class.ctl"], "measurement context"),
    )
    got = _fields_from_xml(path, "My Class.lvclass", "My Class.ctl")
    assert _names(got) == ["alpha", "beta"]


def test_cluster_label_alone_is_not_identity(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _typedef(["My Class.lvclass", "Other.ctl"], "Cluster of class private data"),
    )
    assert _fields_from_xml(path, "My Class.lvclass", "My Class.ctl") is None


def test_member_typedef_of_same_class_not_taken(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _typedef(["My Class.lvclass", "DAQ Tasks.ctl"], "class private data"),
    )
    assert _fields_from_xml(path, "My Class.lvclass", "My Class.ctl") is None


def test_other_class_private_data_not_taken(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _typedef(["Other.lvclass", "My Class.ctl"], "class private data"),
    )
    assert _fields_from_xml(path, "My Class.lvclass", "My Class.ctl") is None


def test_case_only_difference_resolves(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _typedef(["DAQmx Module Runtime.lvclass", "DAQmx Module Runtime.ctl"], "x"),
    )
    got = _fields_from_xml(
        path, "Daqmx Module runtime.lvclass", "DAQmx Module Runtime.ctl"
    )
    assert _names(got) == ["alpha", "beta"]
    got = _fields_from_xml(
        path, "DAQmx Module Runtime.lvclass", "daqmx module runtime.ctl"
    )
    assert _names(got) == ["alpha", "beta"]


def test_by_reference_cluster_deferred(tmp_path: Path) -> None:
    xml = (
        '<?xml version="1.0"?><Root><VCTP><Section>'
        '<TypeDesc Type="Refnum" RefType="DataValueRef" Label="ref"/>'
        + _typedef(["My Class.lvclass", "My Class.ctl"], "x", ids="0")
        + "</Section></VCTP></Root>"
    )
    path = tmp_path / "ref.xml"
    path.write_text(xml)
    assert _fields_from_xml(path, "My Class.lvclass", "My Class.ctl") is None


def test_library_owned_class_resolves(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _typedef(
            ["SDK.lvlib", "Measure Call Context.lvclass", "Measure Call Context.ctl"],
            "measurement context data",
        ),
    )
    got = _fields_from_xml(
        path, "Measure Call Context.lvclass", "Measure Call Context.ctl"
    )
    assert _names(got) == ["alpha", "beta"]


def test_class_and_control_must_be_adjacent(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _typedef(
            ["Other.lvclass", "My Class.lvclass", "DAQ Tasks.ctl", "My Class.ctl"],
            "x",
        ),
    )
    assert _fields_from_xml(path, "My Class.lvclass", "My Class.ctl") is None


def test_non_data_value_refnum_field_kept(tmp_path: Path) -> None:
    xml = (
        '<?xml version="1.0"?><Root><VCTP><Section>'
        '<TypeDesc Type="Refnum" RefType="Queue" Label="Reply Queue"/>'
        + _typedef(["My Class.lvclass", "My Class.ctl"], "x", ids="0")
        + "</Section></VCTP></Root>"
    )
    path = tmp_path / "q.xml"
    path.write_text(xml)
    assert _names(_fields_from_xml(path, "My Class.lvclass", "My Class.ctl")) == [
        "Reply Queue"
    ]
