"""An Error Ring's error out is the error it is configured with, decoded from its
``<StateData>`` (``parser/error_ring.py``; the field layout and its receipts are
in ``docs/_internal/design/labview-binary-format.md``, "Error Ring"). NI's page
(labview-api-ref, vi-lib/errorring/error-ring-xnode.html) names its parts: the
error or warning, the call chain, the code and a description that may take
Format Into String specifiers, one ring input each.

Each test builds a tiny diagram (the ring wired to the VI's error out), checks
every view and runs the generated Python.
"""

from __future__ import annotations

import re

import pytest

from lvkit.parser.error_ring import ErrorRing, decode_error_ring
from tests.error_diagrams import (
    REQUIRED,
    STR,
    ErrorViews,
    Project,
    Vi,
    json_body,
    project,  # noqa: F401  (the fixture)
)

NAME = "Ring.vi"


def _string(text: str) -> bytes:
    data = text.encode()
    return len(data).to_bytes(4, "big") + data


def _state(
    code: int,
    description: str,
    *,
    category: str = "Custom",
    error: bool = True,
    call_chain: bool = True,
    inputs: int = 0,
) -> str:
    """The ``<StateData>`` of a ring; each format input gets the record of the
    one input of ``_FORMAT_RING``."""
    label = f"{code}: {description}"
    blob = (
        _string(label)
        + _string(label)
        + code.to_bytes(4, "big", signed=True)
        + _string(category)
        + _string(description)
        + bytes([error])
        + inputs.to_bytes(4, "big")
        + _input_record() * inputs
        + bytes([call_chain, 1])
    )
    return blob.hex().upper()


def _input_record() -> bytes:
    """The bytes of ``_FORMAT_RING``'s one format input: after its four strings,
    code, error byte and input count, before its two closing bytes."""
    data = bytes.fromhex(_FORMAT_RING)
    pos = 0
    for field in ("string", "string", "code", "string", "string"):
        size = 4 if field == "code" else 4 + int.from_bytes(data[pos : pos + 4], "big")
        pos += size
    return data[pos + 5 : -2]


# Real rings. configurable-dqmh-example ConfigurableQMH/Main.vi: code 5002,
# "%s Module was unable to synchronize events.", one format input.
_FORMAT_RING = (
    "00000027353030323A202573204D6F64756C652077617320756E61626C6520746"
    "F2073796E6368722E2E2E00000031353030323A202573204D6F64756C652077617320756E616"
    "26C6520746F2073796E6368726F6E697A65206576656E74732E0000138A00000006437573746"
    "F6D0000002B2573204D6F64756C652077617320756E61626C6520746F2073796E6368726F6E6"
    "97A65206576656E74732E0100000001140080000000000100080030FFFFFFFF0001000000000"
    "000000000000101"
)
# actor-framework _Message Maker/_support/Wire FP Controls to Accessor
# UnBundler.vi: code 6005, a warning.
_WARNING_RING = (
    "0000002A363030353A204174747269627574657320646F6E2774206D617463682056492070"
    "726F746F74792E2E2E0000002A363030353A204174747269627574657320646F6E2774206D"
    "617463682056492070726F746F747970652E0000177500000006437573746F6D0000002441"
    "74747269627574657320646F6E2774206D617463682056492070726F746F747970652E0000"
    "0000000101"
)


def _views_and_run(
    project: Project,  # noqa: F811
    state: str,
    *args: str,
) -> tuple[ErrorViews, object, str]:
    """Build the ring diagram, return its views, the error its function returns
    for the string inputs ``args``, and the generated source."""
    vi = Vi(NAME)
    out = vi.error_out()
    controls = [vi.control(f"name {i}", STR, REQUIRED) for i in range(len(args))]
    ring = vi.error_ring(state, len(args))
    for i, control in enumerate(controls):
        vi.wire(control, ring[f"parameter {i}"])
    vi.wire(ring["error out"], out)
    project.add(vi)
    views = project.views(NAME)
    package = project.generate(NAME)
    return views, package.function(*args).error_out, package.source


def test_the_views_show_the_ring_as_one_node(project: Project) -> None:  # noqa: F811
    views, _, _ = _views_and_run(project, _FORMAT_RING, "Mod")
    assert views.diagram == (
        "    function xNode_110 : xNode\n"
        "      in   1 : String = name 0\n"
        "      out  0 : Error\n"
        "    error out = xNode_110::0"
    )
    assert views.describe_body() == (
        "\n  ring(name 0: String) -> error out: Error\n\n"
        "## Properties\n  lock_state: unlocked\n\n"
        "## Inputs\n  name 0: String (required)\n\n"
        "## Outputs\n  error out: Error\n\n"
        "## Operations\nxNode"
    )
    assert views.terse.endswith(
        "  block-diagram :\n"
        "    function xNode_110 : xNode\n"
        "      in   1 : String = name 0\n"
        "      out  0 : Error\n"
        "    error out = xNode_110::0"
    )
    assert json_body(views) == (
        '[{"inputs":[{"net":{"bare":"name 0","terminal":"name 0"},"terminal":"1"}],'
        '"kind":"instance","name":"xNode","outputs":[{"bare":"xNode.0",'
        '"node":"xNode","terminal":"0"}],"qualified_name":"xNode","uid":"110"}]'
    )


def test_a_custom_error_with_a_format_input(project: Project) -> None:  # noqa: F811
    _, error, source = _views_and_run(project, _FORMAT_RING, "Mod")
    assert "'%s Module was unable to synchronize events.' % (name_0,)" in source
    assert (error.status, error.code, error.source) == (True, 5002, "")  # type: ignore[attr-defined]
    assert error.message == "Mod Module was unable to synchronize events."  # type: ignore[attr-defined]


def test_a_warning(project: Project) -> None:  # noqa: F811
    _, error, _ = _views_and_run(project, _WARNING_RING)
    assert (error.status, error.code) == (False, 6005)  # type: ignore[attr-defined]
    assert error.message == "Attributes don't match VI prototype."  # type: ignore[attr-defined]


def test_an_ni_code_takes_nis_text(project: Project) -> None:  # noqa: F811
    _, error, _ = _views_and_run(project, _state(7, "", category="LabVIEW"))
    assert (error.status, error.code) == (True, 7)  # type: ignore[attr-defined]
    assert error.message.startswith("File not found.")  # type: ignore[attr-defined]


def test_with_the_call_chain_excluded_the_description_is_in_the_source(
    project: Project,  # noqa: F811
) -> None:
    _, error, _ = _views_and_run(project, _state(6007, "No class.", call_chain=False))
    assert (error.status, error.code) == (True, 6007)  # type: ignore[attr-defined]
    assert (error.source, error.message) == ("<err>No class.", "")  # type: ignore[attr-defined]


def test_code_zero_is_no_error(project: Project) -> None:  # noqa: F811
    _, error, _ = _views_and_run(project, _state(0, "", category="No Error"))
    assert error is None


@pytest.mark.parametrize(
    ("state", "inputs", "reason"),
    [
        ("0000", 0, "Error Ring whose configuration (its StateData) does not decode"),
        (
            _state(5000, "At %T", inputs=1),
            1,
            "Error Ring description uses format specifier %T; only %s is converted",
        ),
    ],
    ids=["undecodable", "timestamp-specifier"],
)
def test_a_ring_that_cannot_be_converted_is_a_gap(
    project: Project,  # noqa: F811
    state: str,
    inputs: int,
    reason: str,
) -> None:
    """Warned at generation, raised where the ring runs (its inputs unwired)."""
    vi = Vi(NAME)
    out = vi.error_out()
    ring = vi.error_ring(state, inputs)
    vi.wire(ring["error out"], out)
    project.add(vi)
    package = project.generate(NAME)
    assert reason in package.source
    with pytest.raises(NotImplementedError, match=re.escape(reason)):
        package.function()


def test_the_format_records_decode() -> None:
    assert decode_error_ring(_FORMAT_RING) == ErrorRing(
        code=5002,
        description="%s Module was unable to synchronize events.",
        is_warning=False,
        include_call_chain=True,
        format_inputs=1,
    )
    assert decode_error_ring(_WARNING_RING) == ErrorRing(
        code=6005,
        description="Attributes don't match VI prototype.",
        is_warning=True,
        include_call_chain=True,
        format_inputs=0,
    )
    # Bytes left over after the fields do not decode.
    assert decode_error_ring(_state(1, "x") + "00") is None
    assert decode_error_ring(None) is None
