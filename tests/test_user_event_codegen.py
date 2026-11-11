"""User events across VIs: Create, Register For Events, Generate, Unregister and
Destroy, and the Event Structure that handles them, generated and run."""

from __future__ import annotations

import json
import queue
from pathlib import Path
from typing import Any

import pytest

from tests.error_diagrams import (
    ERR,
    I32,
    REQUIRED,
    Project,
    Vi,
    project,  # noqa: F401
)
from tests.event_diagrams import (
    _U32,
    USER_EVENT,
    VALUE_CHANGE,
    FrameSpec,
    array_type,
    cluster_type,
    control_reference_type,
    dynamic_events_vi,
    registration_of,
    registration_type,
    user_event_frame,
    value_change_frame,
)
from tests.user_event_diagrams import (
    BOOL,
    ListenFrame,
    bundle_of,
    create_user_event,
    destroy,
    generate_user_event,
    listen,
    register_node,
    unregister,
    user_event_of,
)


def _bool_indicator(vi: Vi, name: str, top: int) -> int:
    return vi.indicator(
        name, BOOL, ddo_class="stdBool", bounds=(top, 200, top + 30, 320)
    )


def _i32_indicator(vi: Vi, name: str, top: int) -> int:
    return vi.indicator(name, I32, bounds=(top, 200, top + 22, 340))


def lifecycle_vi(
    steps: list[str] = (),  # type: ignore[assignment]
    *,
    timeout: int | None = None,
    priority: int | str | None = None,
) -> Vi:
    """Create User Event (data: Boolean ``Stop``), Register For Events, the
    ``steps`` (``unregister`` or ``destroy``, in order), Generate (True) and a While
    Loop whose Event Structure reads ``Stop`` of the user event and ends the loop.
    ``timeout`` (milliseconds, None for unwired) is the Timeout, whose frame sets
    ``Timed Out``. ``priority`` wires a constant (an int) or a control (``"control"``)
    to Generate's priority."""
    vi = Vi("Stopper.vi")
    event = user_event_of(vi, "Stop", BOOL)
    registration = registration_of(vi, [(event, USER_EVENT)])
    seen = _bool_indicator(vi, "Stop Seen", 40)
    timed_out = _bool_indicator(vi, "Timed Out", 80)
    create = create_user_event(vi, event, BOOL)
    vi.wire(vi.bool_constant(False), create["data type"])
    register = register_node(vi, registration, [event])
    vi.wire(create["user event"], register["row 1"])
    vi.wire(create["error out"], register["error in"])
    error = register["error out"]
    forks = [register["registration out"]]
    for step in steps:
        if step == "unregister":
            node = unregister(vi, registration)
            vi.wire(register["registration out"], node["registration"])
        else:
            node = destroy(vi, event)
            vi.wire(create["user event"], node["user event"])
        vi.wire(error, node["error in"])
        error = node["error out"]
    generate = generate_user_event(vi, event, BOOL, priority=priority is not None)
    vi.wire(create["user event"], generate["user event"])
    vi.wire(error, generate["error in"])
    vi.wire(vi.bool_constant(True), generate["data"])
    if priority == "control":
        vi.wire(vi.control("Priority", I32, REQUIRED), generate["priority"])
    elif priority is not None:
        vi.wire(vi.int_constant(int(priority)), generate["priority"])
    loop = listen(
        vi,
        registration,
        [ListenFrame(1, [("Stop", BOOL)], {"Stop": seen})],
        after=[generate["error out"]],
        timeout=timeout,
        timed_out=timed_out,
    )
    vi.wire(forks[0], loop.registration)
    return vi


def test_a_user_event_generated_before_the_loop_ends_it(
    project: Project,  # noqa: F811
) -> None:
    project.add(lifecycle_vi())
    package = project.generate("Stopper.vi")
    assert package.gaps == []
    result = package.function()
    assert result.stop_seen is True
    assert result.timed_out is False
    assert "lvkit" not in package.source
    assert "registration[1][0]" in package.source
    assert "'User Event'" in package.source


def test_the_registration_unregistered_gets_no_event(
    project: Project,  # noqa: F811
) -> None:
    project.add(lifecycle_vi(["unregister"], timeout=50))
    package = project.generate("Stopper.vi")
    assert package.gaps == []
    result = package.function()
    assert (result.stop_seen, result.timed_out) == (False, True)


def test_unregister_twice_and_after_destroy_does_nothing_more(
    project: Project,  # noqa: F811
) -> None:
    project.add(lifecycle_vi(["unregister", "unregister", "destroy"], timeout=50))
    package = project.generate("Stopper.vi")
    result = package.function()
    assert (result.stop_seen, result.timed_out) == (False, True)


def test_a_user_event_destroyed_puts_nothing_even_unregistered_after(
    project: Project,  # noqa: F811
) -> None:
    project.add(lifecycle_vi(["destroy", "unregister"], timeout=50))
    package = project.generate("Stopper.vi")
    assert package.gaps == []
    result = package.function()
    assert (result.stop_seen, result.timed_out) == (False, True)


def test_an_unwired_priority_and_a_constant_normal_priority_generate(
    project: Project,  # noqa: F811
) -> None:
    project.add(lifecycle_vi(priority=1))
    package = project.generate("Stopper.vi")
    assert package.gaps == []
    assert package.function().stop_seen is True


_PRIORITY_GAP = (
    "Generate User Event's priority is wired to something other than the "
    "constant normal (1); only normal priority is converted"
)


@pytest.mark.parametrize("priority", [0, "control"])
def test_any_other_priority_is_a_gap(
    project: Project,  # noqa: F811
    priority: int | str,
) -> None:
    project.add(lifecycle_vi(priority=priority))
    package = project.generate("Stopper.vi")
    assert [g.reason for g in package.gaps] == [_PRIORITY_GAP]
    with pytest.raises(NotImplementedError, match="priority is wired"):
        package.function(*([0] if priority == "control" else []))


def sender_vi(name: str, label: str, data: int, value: int) -> Vi:
    """A VI that generates ``value`` (an I32 or a Boolean) on the user event of the
    data type ``data`` it takes as a parameter."""
    vi = Vi(name)
    event = user_event_of(vi, label, data)
    control = vi.control("Event", event, REQUIRED)
    error_in = vi.error_in()
    error_out = vi.error_out()
    generate = generate_user_event(vi, event, data)
    vi.wire(control, generate["user event"])
    vi.wire(error_in, generate["error in"])
    vi.wire(generate["error out"], error_out)
    constant = vi.int_constant(value) if data == I32 else vi.bool_constant(bool(value))
    vi.wire(constant, generate["data"])
    return vi


def two_vi_caller(name: str = "Caller.vi") -> Vi:
    """Create User Event (I32 ``Count``), Register For Events, a call of
    ``Send.vi`` that generates 7 on it, and the loop whose Event Structure shows the
    data in ``Seen``."""
    vi = Vi(name)
    event = user_event_of(vi, "Count", I32)
    registration = registration_of(vi, [(event, USER_EVENT)])
    seen = _i32_indicator(vi, "Seen", 40)
    create = create_user_event(vi, event, I32)
    vi.wire(vi.int_constant(0), create["data type"])
    register = register_node(vi, registration, [event])
    call = vi.call(
        "Send.vi",
        [
            ("event", 0, event, False),
            ("error in", 1, ERR, False),
            ("error out", 2, ERR, True),
        ],
    )
    vi.wire(create["user event"], register["row 1"], call["event"])
    vi.wire(create["error out"], register["error in"])
    vi.wire(register["error out"], call["error in"])
    loop = listen(
        vi,
        registration,
        [ListenFrame(1, [("Count", I32)], {"Count": seen})],
        after=[call["error out"]],
    )
    vi.wire(register["registration out"], loop.registration)
    return vi


def test_a_vi_generates_on_a_user_event_it_takes_as_a_parameter(
    project: Project,  # noqa: F811
) -> None:
    project.add(sender_vi("Send.vi", "Count", I32, 7))
    project.add(two_vi_caller())
    package = project.generate("Caller.vi")
    assert package.gaps == []
    assert package.function().seen == 7
    sender = Path(str(package.stub("send").__file__)).read_text()
    assert "'User Event'" in sender
    assert "Enum" not in sender
    assert "lvkit" not in sender + package.source


def cluster_row_vi(which: str) -> Vi:
    """Rows: the user event ``Stop``, then a cluster ``Pair`` of ``A`` and ``B``
    (dynIndex 1, 2 for the cluster itself, 3, 4). Generate 5 on ``which`` (``stop``,
    ``a`` or ``b``); the frame of each shows the data."""
    vi = Vi("Cluster.vi")
    types = {n: user_event_of(vi, n.upper(), I32) for n in ("stop", "a", "b")}
    pair = cluster_type(vi, "Pair", [types["a"], types["b"]])
    registration = registration_of(
        vi, [(types["stop"], USER_EVENT), (pair, USER_EVENT)]
    )
    seen = {
        n: _i32_indicator(vi, f"Seen {n}", 40 + 30 * i) for i, n in enumerate(types)
    }
    made = {n: create_user_event(vi, t, I32) for n, t in types.items()}
    for create in made.values():
        vi.wire(vi.int_constant(0), create["data type"])
    pair_value = bundle_of(vi, pair, [types["a"], types["b"]])
    vi.wire(made["a"]["user event"], pair_value["field 0"])
    vi.wire(made["b"]["user event"], pair_value["field 1"])
    register = register_node(vi, registration, [types["stop"], pair])
    vi.wire(made["stop"]["user event"], register["row 1"])
    vi.wire(pair_value["cluster"], register["row 2"])
    generate = generate_user_event(vi, types[which], I32)
    vi.wire(made[which]["user event"], generate["user event"])
    vi.wire(register["error out"], generate["error in"])
    vi.wire(vi.int_constant(5), generate["data"])
    loop = listen(
        vi,
        registration,
        [
            ListenFrame(1, [("Stop", I32)], {"Stop": seen["stop"]}),
            ListenFrame(3, [("A", I32)], {"A": seen["a"]}),
            ListenFrame(4, [("B", I32)], {"B": seen["b"]}),
        ],
        after=[generate["error out"]],
    )
    vi.wire(register["registration out"], loop.registration)
    return vi


@pytest.mark.parametrize("which", ["stop", "a", "b"])
def test_the_frame_of_the_member_of_a_cluster_row_runs(
    project: Project,  # noqa: F811
    which: str,
) -> None:
    project.add(cluster_row_vi(which))
    package = project.generate("Cluster.vi")
    assert package.gaps == []
    result = package.function()
    assert {n: getattr(result, f"seen_{n}") for n in ("stop", "a", "b")} == {
        n: 5 if n == which else 0 for n in ("stop", "a", "b")
    }


def array_row_vi() -> Vi:
    """One row that is an array of user events, taken as the parameter ``Many``; the
    user event ``Target`` is generated on."""
    vi = Vi("ArrayRow.vi")
    event = user_event_of(vi, "Count", I32)
    many_type = array_type(vi, event)
    registration = registration_of(vi, [(many_type, USER_EVENT)])
    many = vi.control("Many", many_type, REQUIRED)
    target = vi.control("Target", event, REQUIRED)
    seen = _i32_indicator(vi, "Seen", 40)
    register = register_node(vi, registration, [many_type])
    vi.wire(many, register["row 1"])
    generate = generate_user_event(vi, event, I32)
    vi.wire(target, generate["user event"])
    vi.wire(register["error out"], generate["error in"])
    vi.wire(vi.int_constant(9), generate["data"])
    loop = listen(
        vi,
        registration,
        [ListenFrame(1, [("Count", I32)], {"Count": seen})],
        after=[generate["error out"]],
    )
    vi.wire(register["registration out"], loop.registration)
    return vi


def test_every_element_of_an_array_row_is_registered(
    project: Project,  # noqa: F811
) -> None:
    project.add(array_row_vi())
    package = project.generate("ArrayRow.vi")
    assert package.gaps == []
    first: list[Any] = []
    second: list[Any] = []
    assert package.function([first, second], second).seen == 9
    assert package.function([first, second], first).seen == 9


def registrations_cluster_vi() -> Vi:
    """Two registrations (``First`` with user event ``A``, ``Second`` with ``B``) in
    a cluster on the structure's dynamic terminal; 1 is generated on ``A`` and 2 on
    ``B`` before the loop. The frame of A carries on, the frame of B ends the loop."""
    vi = Vi("Both.vi")
    a_type = user_event_of(vi, "A", I32)
    b_type = user_event_of(vi, "B", I32)
    first = registration_of(vi, [(a_type, USER_EVENT)], "First")
    second = registration_of(vi, [(b_type, USER_EVENT)], "Second")
    both = vi.add_type(
        f'<TypeDesc Type="Cluster"><TypeDesc TypeID="{first}" />'
        f'<TypeDesc TypeID="{second}" /></TypeDesc>'
    )
    seen_a = _i32_indicator(vi, "Seen A", 40)
    seen_b = _i32_indicator(vi, "Seen B", 70)
    create_a = create_user_event(vi, a_type, I32)
    create_b = create_user_event(vi, b_type, I32)
    vi.wire(vi.int_constant(0), create_a["data type"])
    vi.wire(vi.int_constant(0), create_b["data type"])
    register_a = register_node(vi, first, [a_type])
    register_b = register_node(vi, second, [b_type])
    vi.wire(create_a["user event"], register_a["row 1"])
    vi.wire(create_b["user event"], register_b["row 1"])
    value = bundle_of(vi, both, [first, second])
    vi.wire(register_a["registration out"], value["field 0"])
    vi.wire(register_b["registration out"], value["field 1"])
    vi.wire(register_a["error out"], register_b["error in"])
    gen_a = generate_user_event(vi, a_type, I32)
    gen_b = generate_user_event(vi, b_type, I32)
    vi.wire(create_a["user event"], gen_a["user event"])
    vi.wire(create_b["user event"], gen_b["user event"])
    vi.wire(register_b["error out"], gen_a["error in"])
    vi.wire(gen_a["error out"], gen_b["error in"])
    vi.wire(vi.int_constant(1), gen_a["data"])
    vi.wire(vi.int_constant(2), gen_b["data"])
    loop = listen(
        vi,
        both,
        [
            ListenFrame(1, [("A", I32)], {"A": seen_a}, stop=False),
            ListenFrame(2, [("B", I32)], {"B": seen_b}),
        ],
        after=[gen_b["error out"]],
    )
    vi.wire(value["cluster"], loop.registration)
    return vi


def test_a_cluster_of_registrations_is_read_through_the_vis_events_queue(
    project: Project,  # noqa: F811
) -> None:
    project.add(registrations_cluster_vi())
    package = project.generate("Both.vi")
    assert package.gaps == []
    result = package.function()
    assert (result.seen_a, result.seen_b) == (1, 2)


def static_and_user_vi() -> Vi:
    """A Value Change frame on ``Gain`` and the frame of a user event, in one
    structure: the panel's events and the user event arrive on one queue."""
    vi = Vi("Mixed.vi")
    gain = vi.control("Gain", I32, REQUIRED, bounds=(40, 20, 62, 160))
    event = user_event_of(vi, "Count", I32)
    registration = registration_of(vi, [(event, USER_EVENT)])
    seen = _i32_indicator(vi, "Seen", 40)
    last_new = _i32_indicator(vi, "Last New", 70)
    create = create_user_event(vi, event, I32)
    vi.wire(vi.int_constant(0), create["data type"])
    register = register_node(vi, registration, [event])
    vi.wire(create["user event"], register["row 1"])
    generate = generate_user_event(vi, event, I32)
    vi.wire(create["user event"], generate["user event"])
    vi.wire(register["error out"], generate["error in"])
    vi.wire(vi.int_constant(3), generate["data"])
    change = value_change_frame(
        [gain], '<TypeDesc Type="NumInt32" Label="{label}" />', old=False
    )
    loop = listen(
        vi,
        registration,
        [ListenFrame(1, [("Count", I32)], {"Count": seen})],
        after=[generate["error out"]],
        extra=[change],
    )
    vi.wire(register["registration out"], loop.registration)
    vi.place_inside(last_new, loop.events.frames[-1].scope)
    vi.wire(loop.events.frames[-1].data["NewVal"], last_new)
    return vi


def test_front_panel_events_and_a_user_event_share_the_events_queue(
    project: Project,  # noqa: F811
) -> None:
    project.add(static_and_user_vi())
    package = project.generate("Mixed.vi")
    assert package.gaps == []
    names = package.module.MixedValue
    kinds = package.module.MixedEvent
    panel: queue.Queue = queue.Queue()
    panel.put((names.GAIN, 4, kinds.VALUE_CHANGE, 1))
    result = package.function(0, events=panel)
    assert (result.last_new, result.seen) == (4, 3)


def test_a_row_that_registers_a_control_is_a_gap(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi("Control.vi")
    control_type = control_reference_type(vi, "Button")
    registration = registration_of(vi, [(control_type, VALUE_CHANGE)])
    row = vi.control("Button Ref", control_type, REQUIRED)
    register = register_node(vi, registration, [control_type])
    vi.wire(row, register["row 1"])
    project.add(vi)
    package = project.generate("Control.vi")
    assert [g.reason for g in package.gaps] == [
        "Register For Events row 1 registers a LVObjCtl refnum (dynamic "
        "front-panel events); only User Event rows are converted"
    ]
    with pytest.raises(NotImplementedError, match="row 1 registers"):
        package.function(None)


def test_a_frame_with_no_row_is_a_gap(
    project: Project,  # noqa: F811
) -> None:
    project.add(
        dynamic_events_vi(
            lambda vi: registration_type(vi, [("Start", USER_EVENT)]),
            [user_event_frame(2)],
        )
    )
    package = project.generate("Dynamic.vi")
    assert len(package.gaps) == 1
    assert "has no User Event row 2" in package.gaps[0].reason


def test_a_dynamic_front_panel_event_is_a_gap(
    project: Project,  # noqa: F811
) -> None:
    project.add(
        dynamic_events_vi(
            lambda vi: registration_type(vi, [("Start", USER_EVENT)]),
            [FrameSpec(VALUE_CHANGE, [("Source", _U32)], dyn_index=1)],
        )
    )
    package = project.generate("Dynamic.vi")
    assert len(package.gaps) == 1
    assert "dynamic front-panel event" in package.gaps[0].reason


def cluster_data_vi() -> Vi:
    """A user event whose data is the cluster ``Info`` of ``x`` (I32) and ``y``
    (Boolean): the frame reads each member by its name."""
    vi = Vi("Info.vi")
    x_type = vi.add_type('<TypeDesc Type="NumInt32" Label="x" />')
    y_type = vi.add_type('<TypeDesc Type="Boolean" Label="y" />')
    info = cluster_type(vi, "Info", [x_type, y_type])
    event = user_event_of(vi, "Info", info)
    registration = registration_of(vi, [(event, USER_EVENT)])
    seen_x = _i32_indicator(vi, "Seen x", 40)
    seen_y = _bool_indicator(vi, "Seen y", 70)
    create = create_user_event(vi, event, info)
    register = register_node(vi, registration, [event])
    vi.wire(create["user event"], register["row 1"])
    data = bundle_of(vi, info, [x_type, y_type])
    vi.wire(vi.int_constant(8), data["field 0"])
    vi.wire(vi.bool_constant(True), data["field 1"])
    generate = generate_user_event(vi, event, info)
    vi.wire(create["user event"], generate["user event"])
    vi.wire(register["error out"], generate["error in"])
    vi.wire(data["cluster"], generate["data"])
    loop = listen(
        vi,
        registration,
        [ListenFrame(1, [("x", I32), ("y", BOOL)], {"x": seen_x, "y": seen_y})],
        after=[generate["error out"]],
    )
    vi.wire(register["registration out"], loop.registration)
    return vi


def test_the_members_of_a_cluster_of_event_data_are_read_by_name(
    project: Project,  # noqa: F811
) -> None:
    project.add(cluster_data_vi())
    package = project.generate("Info.vi")
    assert package.gaps == []
    result = package.function()
    assert (result.seen_x, result.seen_y) == (8, True)


_DESCRIBE = """\

  stopper() -> Stop Seen: TF, Timed Out: TF

## Properties
  lock_state: unlocked

## Inputs
  (none)

## Outputs
  Stop Seen: TF
  Timed Out: TF

## Constants
  (unnamed): TF = False
  (unnamed): TF = True

## Control Flow
  While loop (stops when Event Structure tunnel)
    \\ Event structure (2 frames)

## Operations
Create User Event [prim 2073]
Reg Events
Generate User Event [prim 2074]
While Loop
  Event Structure
    Frame [0] Timeout:
      Event Data Node
      (unnamed): TF = True
      (unnamed): TF = True
    Frame [1] <Stop>: User Event:
      Event Data Node
      (unnamed): TF = True
  Tunnels:
    in  tunnel
    in  tunnel (error out)"""

_TERSE = """\
vi Stopper.vi :
  front-panel :
    pattern : 4800
    out  Stop Seen : TF @0
    out  Timed Out : TF @1
  block-diagram :
    function Create_User_Event_114 : Create User Event
      in   user event datatype : TF                   = False
      in   error in (no error) : Error                default
      out  user event          : UserEvent refnum{TF}
      out  error out           : Error
    function Reg_Events_128 : Reg Events
      in   0 : EventReg refnum{UserEvent refnum{TF}} default
      in   2 : Error                                 = Create_User_Event_114::error out
      in   4 : UserEvent refnum{TF}                  = Create_User_Event_114::user event
      out  1 : EventReg refnum{UserEvent refnum{TF}}
      out  3 : Error
    function Generate_User_Event_141 : Generate User Event
      in   user event     : UserEvent refnum{TF} = Create_User_Event_114::user event
      in   event data     : TF                   = True
      in   error in       : Error                = Reg_Events_128::3
      out  user event out : UserEvent refnum{TF}
      out  error out      : Error
    while-loop (stop if true) :
      loop_147::in1 = Generate_User_Event_141::error out
      event-structure :
        event_158::dynamic = Reg_Events_128::1
        frame "[0] Timeout" :
          function Event_Data_Node_175 : Event Data Node
            in   0 : Cluster{ Source : U32, Type : U32, Time : U32 } default
          Timed Out = True
          event_158::out0 = True
        frame "[1] <Stop>: User Event" :
          function Event_Data_Node_178 : Event Data Node
            in   0    : Cluster{ Source : U32, Type : U32, Time : U32, UsrEvtRef : UserEvent refnum, Stop : TF } default
            out  Stop : TF
          Stop Seen = Event_Data_Node_178::Stop
          event_158::out0 = True
      loop_147::condition = event_158::out0"""  # noqa: E501


def test_the_views_of_a_user_event_vi_are_what_they_were_before_code_generation(
    project: Project,  # noqa: F811
) -> None:
    project.add(lifecycle_vi())
    views = project.views("Stopper.vi")
    assert views.describe_body() == _DESCRIBE
    assert views.terse == _TERSE
    assert '"event_rows": [{"name": "Stop", "event_type": 1000}]' in json.dumps(
        views.json
    )


def nested_data_vi() -> Vi:
    """A cluster of two registrations; the first user event carries the cluster
    ``Params`` of ``Value`` (a cluster of ``a`` and ``b``) and ``Closed``. Its frame
    reads ``Value`` and ``Closed``: the drawer of ``Closed`` stores the index 7, as
    the cluster ``Value`` takes the slots 4 to 6 (``UsrEvtRef`` is 3)."""
    vi = Vi("Nested.vi")
    a_type = vi.add_type('<TypeDesc Type="NumInt32" Label="a" />')
    b_type = vi.add_type('<TypeDesc Type="NumInt32" Label="b" />')
    value = cluster_type(vi, "Value", [a_type, b_type])
    closed = vi.add_type('<TypeDesc Type="Boolean" Label="Closed" />')
    params = cluster_type(vi, "Params", [value, closed])
    first_event = user_event_of(vi, "Params", params)
    second_event = user_event_of(vi, "Other", I32)
    first = registration_of(vi, [(first_event, USER_EVENT)], "First")
    second = registration_of(vi, [(second_event, USER_EVENT)], "Second")
    both = vi.add_type(
        f'<TypeDesc Type="Cluster"><TypeDesc TypeID="{first}" />'
        f'<TypeDesc TypeID="{second}" /></TypeDesc>'
    )
    seen_value = vi.indicator("Seen Value", value, bounds=(40, 200, 90, 340))
    seen_closed = _bool_indicator(vi, "Seen Closed", 100)
    made = [create_user_event(vi, first_event, params)]
    made.append(create_user_event(vi, second_event, I32))
    vi.wire(vi.int_constant(0), made[1]["data type"])
    register_a = register_node(vi, first, [first_event])
    register_b = register_node(vi, second, [second_event])
    vi.wire(made[0]["user event"], register_a["row 1"])
    vi.wire(made[1]["user event"], register_b["row 1"])
    registrations = bundle_of(vi, both, [first, second])
    vi.wire(register_a["registration out"], registrations["field 0"])
    vi.wire(register_b["registration out"], registrations["field 1"])
    vi.wire(register_a["error out"], register_b["error in"])
    inner = bundle_of(vi, value, [I32, I32])
    vi.wire(vi.int_constant(4), inner["field 0"])
    vi.wire(vi.int_constant(5), inner["field 1"])
    data = bundle_of(vi, params, [value, BOOL])
    vi.wire(inner["cluster"], data["field 0"])
    vi.wire(vi.bool_constant(True), data["field 1"])
    generate = generate_user_event(vi, first_event, params)
    vi.wire(made[0]["user event"], generate["user event"])
    vi.wire(register_b["error out"], generate["error in"])
    vi.wire(data["cluster"], generate["data"])
    loop = listen(
        vi,
        both,
        [
            ListenFrame(
                1,
                [
                    (
                        "Value",
                        f'<TypeDesc Type="Cluster" Label="Value">'
                        f'<TypeDesc TypeID="{a_type}" />'
                        f'<TypeDesc TypeID="{b_type}" /></TypeDesc>',
                    ),
                    ("Closed", BOOL),
                ],
                shows={"Value": seen_value, "Closed": seen_closed},
                flat={"Value": 4, "Closed": 7},
            ),
            ListenFrame(2, [("Other", I32)]),
        ],
        after=[generate["error out"]],
    )
    vi.wire(registrations["cluster"], loop.registration)
    return vi


def test_event_data_after_a_cluster_field_is_found_by_its_depth_first_index(
    project: Project,  # noqa: F811
) -> None:
    project.add(nested_data_vi())
    package = project.generate("Nested.vi")
    assert package.gaps == []
    result = package.function()
    assert (result.seen_value, result.seen_closed) == ((4, 5), True)
