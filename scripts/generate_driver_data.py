#!/usr/bin/env python3
"""Generate data/drivers/*.json from Python library APIs.

Maps LabVIEW hardware driver VIs to Python equivalents:
  - DAQmx VIs -> nidaqmx
  - VISA VIs -> pyvisa
  - Serial VIs -> pyserial
  - NI-SCOPE VIs -> niscope
  - NI-FGEN VIs -> nifgen
  - NI-DCPower VIs -> nidcpower
  - NI-DMM VIs -> nidmm
  - NI-SWITCH VIs -> niswitch
  - NI-Digital VIs -> nidigital

Usage:
    python scripts/generate_driver_data.py
    python scripts/generate_driver_data.py --output data/drivers
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

# =============================================================
# Helpers
# =============================================================


def _t(
    name: str, idx: int | None, direction: str, param: str,
) -> dict[str, Any]:
    """Build a terminal dict.

    Use idx=None for unverified indices. The auto-update system will
    fill in correct indices from actual VI dataflow observations.
    """
    d: dict[str, Any] = {
        "name": name,
        "direction": direction,
        "python_param": param,
    }
    if idx is not None:
        d["index"] = idx
    return d


def _entry(
    name: str,
    code: str,
    desc: str,
    terminals: list[dict[str, Any]],
    imports: list[str],
    ref: dict[str, str] | None = None,
    match: list[str] | None = None,
    poly_names: list[str] | None = None,
) -> dict[str, Any]:
    """Build a VIEntry-compatible dict.

    Args:
        poly_names: polySelector buf names that map to this variant.
            These are the exact strings from the VI's polySelector dropdown.
            Used to match polyIUse nodes to the correct variant entry.
    """
    e: dict[str, Any] = {
        "name": name,
        "description": desc,
        "python_code": code,
        "inline": True,
        "imports": imports,
        "status": "mapped",
        "terminals": terminals,
    }
    if ref:
        e["ref_terminals"] = ref
    if match:
        e["match_names"] = match
    if poly_names:
        e["poly_selector_names"] = poly_names
    return e


def _cat(name: str, entries: list[dict[str, Any]]) -> dict:
    """Build a category dict."""
    return {
        "category": name,
        "count": len(entries),
        "entries": entries,
    }


# =============================================================
# Common terminal patterns
# =============================================================

# DAQmx — only indices verified from actual VI observations (In.vi)
_TI = _t("task in", 0, "input", "task_in")        # verified: In.vi
_TO4 = _t("task out", 4, "output", "task_out")     # verified: In.vi
_TO8 = _t("task out", None, "output", "task_out")   # unverified
_CI = _t("task/channels in", 0, "input", "task_in")  # verified: In.vi
_PC = _t("physical channels", 5, "input", "physical_channel")  # verified: In.vi
_MN = _t("minimum value", None, "input", "min_val")
_MX = _t("maximum value", None, "input", "max_val")
_RP = {"task_out": "passthrough_from:task_in"}
_IMP_DAQ = ["import nidaqmx"]

# VISA / pyvisa — indices unverified
_VI = _t("VISA resource name", None, "input", "instr")
_VO = _t("VISA resource name out", None, "output", "instr_out")
_VR = {"instr_out": "passthrough_from:instr"}
_IMP_VISA = ["import pyvisa"]

# NI modular (session-based) — indices unverified
_SI = _t("instrument handle", None, "input", "session")
_SO = _t("instrument handle out", None, "output", "session_out")
_SR = {"session_out": "passthrough_from:session"}


def _ni_sess(pkg: str) -> list[str]:
    return [f"import {pkg}"]


# =============================================================
# DAQmx (nidaqmx)
# =============================================================


def _daqmx_entries() -> list[dict[str, Any]]:
    e: list[dict[str, Any]] = []

    # --- Task lifecycle ---
    e.append(_entry(
        "DAQmx Create Task.vi",
        "{task_out} = nidaqmx.Task({task_name})",
        "Creates a DAQmx task.",
        [_t("task name", 1, "input", "task_name"), _TO4],  # verified: In.vi
        _IMP_DAQ, _RP,
    ))
    for vi, method, desc in [
        ("DAQmx Start Task.vi", "start", "Starts task."),
        ("DAQmx Stop Task.vi", "stop", "Stops task."),
        ("DAQmx Is Task Done.vi", "is_task_done", "Checks done."),
    ]:
        e.append(_entry(vi, f"{{task_in}}.{method}()",
                        desc, [_TI, _TO4], _IMP_DAQ, _RP))
    e.append(_entry(
        "DAQmx Clear Task.vi", "{task_in}.close()",
        "Clears (closes) the task.", [_TI], _IMP_DAQ,
    ))
    e.append(_entry(
        "DAQmx Wait Until Done.vi",
        "{task_in}.wait_until_done(timeout={timeout})",
        "Waits for the task to finish.",
        [_TI, _t("timeout (s)", None, "input", "timeout"), _TO4],
        _IMP_DAQ, _RP,
    ))

    # --- Create Virtual Channel (base polymorphic) ---
    # The base VI dispatches to variant; we map it generically
    # with wide terminal coverage so any wiring pattern matches.
    e.append(_entry(
        "DAQmx Create Virtual Channel.vi",
        "{task_in}.ai_channels.add_ai_voltage_chan("
        "{physical_channel})",
        "Creates a virtual channel (polymorphic base).",
        [_CI,
         _t("physical channels", 5, "input",       # verified: In.vi
            "physical_channel"),
         _TO4,
         _t("task out", 15, "output", "task_out")],  # verified: In.vi
        _IMP_DAQ, _RP,
    ))

    # --- Read (polymorphic) ---
    e.append(_entry(
        "DAQmx Read.vi",
        "{data} = {task_in}.read("
        "number_of_samples_per_channel={num_samps})",
        "Reads samples from the task.",
        [_TI,
         _t("number of samples per channel", None, "input",
            "num_samps"),
         _t("data", None, "output", "data"),
         _TO8],
        _IMP_DAQ, _RP,
    ))
    for suffix, desc in [
        ("Analog 1D DBL 1Chan NSamp",
         "Reads 1-chan analog 1D."),
        ("Analog 1D DBL NChan 1Samp",
         "Reads N-chan analog 1-samp."),
        ("Analog 2D DBL NChan NSamp",
         "Reads N-chan analog 2D."),
        ("Digital 1D Bool 1Chan 1Samp",
         "Reads 1-chan digital bool."),
        ("Digital 1D U32 1Chan NSamp",
         "Reads 1-chan digital U32."),
        ("Counter 1D DBL 1Chan NSamp",
         "Reads counter data."),
    ]:
        vi = f"DAQmx Read ({suffix}).vi"
        e.append(_entry(
            vi,
            "{data} = {task_in}.read("
            "number_of_samples_per_channel={num_samps})",
            desc,
            [_TI,
             _t("number of samples per channel", None, "input",
                "num_samps"),
             _t("data", None, "output", "data"),
             _TO8],
            _IMP_DAQ, _RP, [vi],
        ))

    # --- Write (polymorphic) ---
    e.append(_entry(
        "DAQmx Write.vi",
        "{task_in}.write({data})",
        "Writes samples to the task.",
        [_TI, _t("data", 7, "input", "data"), _TO4],  # verified: In.vi
        _IMP_DAQ, _RP,
    ))
    for suffix, desc in [
        ("Analog 1D DBL 1Chan NSamp",
         "Writes 1-chan analog 1D."),
        ("Analog 2D DBL NChan NSamp",
         "Writes N-chan analog 2D."),
        ("Digital 1D Bool 1Chan 1Samp",
         "Writes 1-chan digital bool."),
        ("Digital 1D U32 1Chan NSamp",
         "Writes 1-chan digital U32."),
    ]:
        vi = f"DAQmx Write ({suffix}).vi"
        e.append(_entry(
            vi, "{task_in}.write({data})", desc,
            [_TI, _t("data", 7, "input", "data"), _TO4],  # verified: In.vi
            _IMP_DAQ, _RP, [vi],
        ))

    # --- Create Channel (polymorphic) ---
    # 4th element = polySelector names from the VI's dropdown (exact XML strings)
    chan_variants: list[tuple[str, str, list[dict], list[str]]] = [
        ("AI Voltage", "ai_channels.add_ai_voltage_chan",
         [_MN, _MX], ["AI Voltage"]),
        ("AI Current", "ai_channels.add_ai_current_chan",
         [_MN, _MX], ["AI Current", "AI Current RMS"]),
        ("AI Thermocouple",
         "ai_channels.add_ai_thrmcpl_chan", [_MN, _MX], ["AI Temp TC"]),
        ("AI RTD", "ai_channels.add_ai_rtd_chan",
         [_MN, _MX], ["AI Temp RTD"]),
        ("AI Strain Gage",
         "ai_channels.add_ai_strain_gage_chan", [_MN, _MX],
         ["AI Strain Gage"]),
        ("AI Accelerometer",
         "ai_channels.add_ai_accel_chan", [_MN, _MX], ["AI Accelerometer"]),
        ("AI Bridge",
         "ai_channels.add_ai_bridge_chan", [_MN, _MX], ["AI Bridge"]),
        ("AI Force Bridge Table",
         "ai_channels.add_ai_force_bridge_table_chan",
         [_MN, _MX], []),
        ("AI Force IEPE",
         "ai_channels.add_ai_force_iepe_chan", [_MN, _MX],
         ["AI Force IEPE"]),
        ("AI Microphone",
         "ai_channels.add_ai_microphone_chan", [_MN, _MX],
         ["AI Microphone"]),
        ("AI Pressure Bridge Table",
         "ai_channels.add_ai_pressure_bridge_table_chan",
         [_MN, _MX], []),
        ("AI Resistance",
         "ai_channels.add_ai_resistance_chan", [_MN, _MX],
         ["AI Resistance"]),
        ("AI Torque Bridge Table",
         "ai_channels.add_ai_torque_bridge_table_chan",
         [_MN, _MX], []),
        ("AI Velocity IEPE",
         "ai_channels.add_ai_velocity_iepe_chan",
         [_MN, _MX], ["AI Velocity IEPE"]),
        ("AI Temperature Built-In Sensor",
         "ai_channels.add_ai_temp_built_in_sensor_chan",
         [], ["AI Temp Built-In Sensor"]),
        ("AO Voltage", "ao_channels.add_ao_voltage_chan",
         [_MN, _MX], ["AO Voltage"]),
        ("AO Current", "ao_channels.add_ao_current_chan",
         [_MN, _MX], ["AO Current"]),
        ("AO FuncGen",
         "ao_channels.add_ao_func_gen_chan", [], ["AO FuncGen"]),
        ("DI Line", "di_channels.add_di_chan", [], ["Digital Input"]),
        ("DI Channel", "di_channels.add_di_chan", [], []),
        ("DO Line", "do_channels.add_do_chan", [], ["Digital Output"]),
        ("DO Channel", "do_channels.add_do_chan", [], []),
        ("CI Count Edges",
         "ci_channels.add_ci_count_edges_chan", [], ["CI Cnt Edges"]),
        ("CI Freq", "ci_channels.add_ci_freq_chan",
         [_MN, _MX], ["CI Freq"]),
        ("CI Period", "ci_channels.add_ci_period_chan",
         [_MN, _MX], ["CI Period"]),
        ("CI Pulse Width",
         "ci_channels.add_ci_pulse_width_chan",
         [_MN, _MX], ["CI Pulse Width"]),
        ("CI Two Edge Sep",
         "ci_channels.add_ci_two_edge_sep_chan",
         [_MN, _MX], ["CI Two Edge Separation"]),
        ("CO Pulse Freq",
         "co_channels.add_co_pulse_chan_freq",
         [_t("frequency", None, "input", "freq"),
          _t("duty cycle", None, "input", "duty_cycle")],
         ["CO Pulse Freq"]),
        ("CO Pulse Time",
         "co_channels.add_co_pulse_chan_time",
         [_t("high time", None, "input", "high_time"),
          _t("low time", None, "input", "low_time")],
         ["CO Pulse Time"]),
        ("CO Pulse Ticks",
         "co_channels.add_co_pulse_chan_ticks",
         [_t("high ticks", None, "input", "high_ticks"),
          _t("low ticks", None, "input", "low_ticks")],
         ["CO Pulse Ticks"]),
    ]
    for variant, method, extras, poly_names in chan_variants:
        vi = f"DAQmx Create Virtual Channel ({variant}).vi"
        alt = f"DAQmx Create Channel ({variant}).vi"
        params = ", ".join(
            f"{t['python_param']}={{{t['python_param']}}}"
            for t in extras
        )
        code = f"{{task_in}}.{method}({{physical_channel}}"
        if params:
            code += f", {params}"
        code += ")"
        terms = [_CI, _PC] + extras + [_TO4]  # task_out at idx 4: verified In.vi
        e.append(_entry(
            vi, code, f"Creates {variant} channel.",
            terms, _IMP_DAQ, _RP, [alt], poly_names,
        ))

    # --- Timing (polymorphic) ---
    timing_variants: list[tuple[str, str, list[dict]]] = [
        ("Sample Clock", "timing.cfg_samp_clk_timing", [
            _t("rate", None, "input", "rate"),
            _t("sample mode", None, "input", "sample_mode"),
            _t("samples per channel", None, "input",
               "samps_per_chan"),
        ]),
        ("Implicit", "timing.cfg_implicit_timing", [
            _t("sample mode", None, "input", "sample_mode"),
            _t("samples per channel", None, "input",
               "samps_per_chan"),
        ]),
    ]
    for variant, method, terms in timing_variants:
        vi = f"DAQmx Timing ({variant}).vi"
        params = ", ".join(
            f"{t['python_param']}={{{t['python_param']}}}"
            for t in terms
        )
        code = f"{{task_in}}.{method}({params})"
        e.append(_entry(
            vi, code, f"Configures {variant} timing.",
            [_CI] + terms + [_TO8],
            _IMP_DAQ, _RP, ["DAQmx Timing.vi"],
        ))

    # --- Triggers ---
    trig_variants: list[tuple[str, str, str, list[dict]]] = [
        ("Digital Edge",
         "cfg_dig_edge_start_trig",
         "Configures digital edge start trigger.",
         [_t("source", None, "input", "trigger_source")]),
        ("Analog Edge",
         "cfg_anlg_edge_start_trig",
         "Configures analog edge start trigger.",
         [_t("source", None, "input", "trigger_source"),
          _t("level", None, "input", "trigger_level")]),
        ("Analog Window",
         "cfg_anlg_window_start_trig",
         "Configures analog window start trigger.",
         [_t("source", None, "input", "trigger_source"),
          _t("window top", None, "input", "window_top"),
          _t("window bottom", None, "input", "window_bottom")]),
        ("Digital Pattern",
         "cfg_dig_pattern_start_trig",
         "Configures digital pattern start trigger.",
         [_t("source", None, "input", "trigger_source"),
          _t("pattern", None, "input", "trigger_pattern")]),
        ("None", "disable_start_trig",
         "Disables start trigger.", []),
    ]
    for variant, method, desc, terms in trig_variants:
        vi = f"DAQmx Trigger ({variant}).vi"
        full = f"triggers.start_trigger.{method}"
        params = ", ".join(
            f"{t['python_param']}={{{t['python_param']}}}"
            for t in terms
        )
        code = f"{{task_in}}.{full}({params})"
        e.append(_entry(
            vi, code, desc,
            [_CI] + terms + [_TO8],
            _IMP_DAQ, _RP,
            ["DAQmx Configure Trigger.vi"],
        ))

    # --- Reference Triggers ---
    ref_trig: list[tuple[str, str, str, list[dict]]] = [
        ("Digital Edge",
         "cfg_dig_edge_ref_trig",
         "Configures digital edge reference trigger.",
         [_t("source", None, "input", "trigger_source"),
          _t("pretrigger samples", None, "input",
             "pretrigger_samples")]),
        ("Analog Edge",
         "cfg_anlg_edge_ref_trig",
         "Configures analog edge reference trigger.",
         [_t("source", None, "input", "trigger_source"),
          _t("pretrigger samples", None, "input",
             "pretrigger_samples"),
          _t("level", None, "input", "trigger_level")]),
        ("None", "disable_ref_trig",
         "Disables reference trigger.", []),
    ]
    for variant, method, desc, terms in ref_trig:
        vi = f"DAQmx Reference Trigger ({variant}).vi"
        full = f"triggers.reference_trigger.{method}"
        params = ", ".join(
            f"{t['python_param']}={{{t['python_param']}}}"
            for t in terms
        )
        code = f"{{task_in}}.{full}({params})"
        e.append(_entry(vi, code, desc,
                        [_CI] + terms + [_TO8],
                        _IMP_DAQ, _RP))

    # --- Buffer configuration ---
    e.append(_entry(
        "DAQmx Configure Input Buffer.vi",
        "{task_in}.in_stream.input_buf_size = {buffer_size}",
        "Configures the input buffer size.",
        [_TI,
         _t("buffer size", None, "input", "buffer_size"),
         _TO4],
        _IMP_DAQ, _RP,
    ))
    e.append(_entry(
        "DAQmx Configure Output Buffer.vi",
        "{task_in}.out_stream.output_buf_size = {buffer_size}",
        "Configures the output buffer size.",
        [_TI,
         _t("buffer size", None, "input", "buffer_size"),
         _TO4],
        _IMP_DAQ, _RP,
    ))

    return e


# =============================================================
# VISA (pyvisa) — all functions from vilib PDF
# =============================================================


def _visa_entries() -> list[dict[str, Any]]:
    e: list[dict[str, Any]] = []

    e.append(_entry(
        "VISA Open.vi",
        "{instr_out} = pyvisa.ResourceManager()"
        ".open_resource({visa_resource_name})",
        "Opens a VISA session to a resource.",
        [_t("VISA resource name", None, "input",
            "visa_resource_name"),
         _t("timeout", None, "input", "open_timeout"),
         _VO], _IMP_VISA,
    ))
    e.append(_entry(
        "VISA Write.vi",
        "{return_count} = {instr}.write({write_buffer})",
        "Writes data to a device.",
        [_VI, _t("write buffer", None, "input", "write_buffer"),
         _VO, _t("return count", None, "output", "return_count")],
        _IMP_VISA, _VR,
    ))
    e.append(_entry(
        "VISA Read.vi",
        "{read_buffer} = {instr}.read()",
        "Reads data from a device.",
        [_VI, _t("byte count", None, "input", "byte_count"),
         _VO,
         _t("read buffer", None, "output", "read_buffer")],
        _IMP_VISA, _VR,
    ))
    e.append(_entry(
        "VISA Close.vi",
        "{instr}.close()",
        "Closes a VISA session.",
        [_VI], _IMP_VISA,
    ))
    e.append(_entry(
        "VISA Find Resource.vi",
        "{find_list} = pyvisa.ResourceManager()"
        ".list_resources({expression})",
        "Finds VISA resources matching expression.",
        [_t("expression", None, "input", "expression"),
         _t("find list", None, "output", "find_list")],
        _IMP_VISA,
    ))
    e.append(_entry(
        "VISA Write From File.vi",
        "with open({file_path}, 'rb') as _f:\n"
        "    {instr}.write_raw(_f.read())",
        "Writes file contents to a device.",
        [_VI,
         _t("file path", None, "input", "file_path"),
         _VO], _IMP_VISA, _VR,
    ))
    e.append(_entry(
        "VISA Read To File.vi",
        "with open({file_path}, 'wb') as _f:\n"
        "    _f.write({instr}.read_raw())",
        "Reads data from device to file.",
        [_VI,
         _t("file path", None, "input", "file_path"),
         _VO], _IMP_VISA, _VR,
    ))
    e.append(_entry(
        "VISA Clear.vi",
        "{instr}.clear()",
        "Clears a device.",
        [_VI, _VO], _IMP_VISA, _VR,
    ))
    e.append(_entry(
        "VISA Lock.vi",
        "{instr}.lock()",
        "Locks a VISA resource.",
        [_VI, _VO], _IMP_VISA, _VR,
    ))
    e.append(_entry(
        "VISA Unlock.vi",
        "{instr}.unlock()",
        "Unlocks a VISA resource.",
        [_VI, _VO], _IMP_VISA, _VR,
    ))
    e.append(_entry(
        "VISA Read STB.vi",
        "{status_byte} = {instr}.read_stb()",
        "Reads service request status byte.",
        [_VI, _VO,
         _t("status byte", None, "output", "status_byte")],
        _IMP_VISA, _VR,
    ))
    e.append(_entry(
        "VISA Assert Trigger.vi",
        "{instr}.assert_trigger()",
        "Asserts a software or hardware trigger.",
        [_VI, _VO], _IMP_VISA, _VR,
    ))
    e.append(_entry(
        "VISA Flush I/O Buffer.vi",
        "{instr}.flush("
        "pyvisa.constants.BufferOperation"
        ".discard_read_buffer_no_io)",
        "Flushes I/O buffers.",
        [_VI,
         _t("mask", None, "input", "mask"),
         _VO], _IMP_VISA, _VR,
    ))
    e.append(_entry(
        "VISA Set Timeout.vi",
        "{instr}.timeout = {timeout_value}",
        "Sets the timeout for a VISA resource.",
        [_VI,
         _t("timeout value (ms)", None, "input", "timeout_value"),
         _VO], _IMP_VISA, _VR,
    ))
    e.append(_entry(
        "VISA Configure Serial Port.vi",
        "{instr}.baud_rate = {baud_rate}\n"
        "{instr}.data_bits = {data_bits}\n"
        "{instr}.parity = {parity}\n"
        "{instr}.stop_bits = {stop_bits}",
        "Configures a serial port.",
        [_VI,
         _t("baud rate", None, "input", "baud_rate"),
         _t("data bits", None, "input", "data_bits"),
         _t("parity", None, "input", "parity"),
         _t("stop bits", None, "input", "stop_bits"),
         _t("VISA resource name out", None, "output",
            "instr_out")],
        _IMP_VISA, _VR,
    ))
    e.append(_entry(
        "VISA Set I/O Buffer Size.vi",
        "{instr}.set_visa_attribute("
        "pyvisa.constants.ResourceAttribute"
        ".send_buffer_size, {size})",
        "Sets I/O buffer size.",
        [_VI,
         _t("mask", None, "input", "mask"),
         _t("size", None, "input", "size"),
         _VO], _IMP_VISA, _VR,
    ))
    e.append(_entry(
        "VISA Enable Event.vi",
        "{instr}.enable_event("
        "{event_type}, {mechanism})",
        "Enables event notification.",
        [_VI,
         _t("event type", None, "input", "event_type"),
         _t("mechanism", None, "input", "mechanism"),
         _VO], _IMP_VISA, _VR,
    ))
    e.append(_entry(
        "VISA Disable Event.vi",
        "{instr}.disable_event("
        "{event_type}, {mechanism})",
        "Disables event notification.",
        [_VI,
         _t("event type", None, "input", "event_type"),
         _t("mechanism", None, "input", "mechanism"),
         _VO], _IMP_VISA, _VR,
    ))

    return e


# =============================================================
# Serial (pyserial)
# =============================================================


def _serial_entries() -> list[dict[str, Any]]:
    imp = ["import serial"]
    si = _t("serial port", None, "input", "ser")
    so = _t("serial port out", None, "output", "ser_out")
    sr = {"ser_out": "passthrough_from:ser"}

    return [
        _entry(
            "Serial Port Init.vi",
            "{ser_out} = serial.Serial("
            "port={port}, baudrate={baud_rate})",
            "Opens and configures a serial port.",
            [_t("port name", None, "input", "port"),
             _t("baud rate", None, "input", "baud_rate"),
             so], imp,
        ),
        _entry(
            "Serial Port Write.vi",
            "{ser}.write({data})",
            "Writes data to serial port.",
            [si, _t("data", None, "input", "data"), so],
            imp, sr,
        ),
        _entry(
            "Serial Port Read.vi",
            "{read_data} = {ser}.read({byte_count})",
            "Reads data from serial port.",
            [si, _t("byte count", None, "input", "byte_count"),
             so,
             _t("data", None, "output", "read_data")],
            imp, sr,
        ),
        _entry(
            "Serial Port Close.vi",
            "{ser}.close()",
            "Closes a serial port.",
            [si], imp,
        ),
        _entry(
            "Serial Port Bytes at Port.vi",
            "{bytes_at_port} = {ser}.in_waiting",
            "Returns bytes in the input buffer.",
            [si,
             _t("bytes at port", None, "output",
                "bytes_at_port"),
             so],
            imp, sr,
        ),
        _entry(
            "Serial Port Flush.vi",
            "{ser}.reset_input_buffer()\n"
            "{ser}.reset_output_buffer()",
            "Flushes serial port buffers.",
            [si, so], imp, sr,
        ),
    ]


# =============================================================
# NI Modular Instruments
#
# All follow the same session-based pattern:
#   Session(resource_name) -> configure -> initiate ->
#   fetch/measure -> abort -> close
# =============================================================


def _ni_session_lifecycle(
    prefix: str,
    pkg: str,
    extra_init_terms: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Generate standard init/close/reset/self_test/self_cal."""
    imp = _ni_sess(pkg)
    init_terms = [
        _t("resource name", None, "input", "resource_name"),
    ] + (extra_init_terms or []) + [_SO]
    return [
        _entry(
            f"{prefix} Initialize.vi",
            f"{{session_out}} = {pkg}.Session("
            "{resource_name})",
            f"Opens a {prefix} session.",
            init_terms, imp,
        ),
        _entry(
            f"{prefix} Close.vi",
            "{session}.close()",
            f"Closes a {prefix} session.",
            [_SI], imp,
        ),
        _entry(
            f"{prefix} Reset.vi",
            "{session}.reset()",
            f"Resets the {prefix} instrument.",
            [_SI, _SO], imp, _SR,
        ),
        _entry(
            f"{prefix} Self-Test.vi",
            "{result} = {session}.self_test()",
            f"Runs {prefix} self-test.",
            [_SI, _SO,
             _t("result", None, "output", "result")],
            imp, _SR,
        ),
        _entry(
            f"{prefix} Self-Cal.vi",
            "{session}.self_cal()",
            f"Runs {prefix} self-calibration.",
            [_SI, _SO], imp, _SR,
        ),
        _entry(
            f"{prefix} Initiate.vi",
            "{session}.initiate()",
            f"Initiates {prefix} acquisition/generation.",
            [_SI, _SO], imp, _SR,
        ),
        _entry(
            f"{prefix} Abort.vi",
            "{session}.abort()",
            f"Aborts {prefix} acquisition/generation.",
            [_SI, _SO], imp, _SR,
        ),
        _entry(
            f"{prefix} Commit.vi",
            "{session}.commit()",
            f"Commits {prefix} configuration.",
            [_SI, _SO], imp, _SR,
        ),
        _entry(
            f"{prefix} Disable.vi",
            "{session}.disable()",
            f"Disables {prefix} output.",
            [_SI, _SO], imp, _SR,
        ),
    ]


# --- NI-SCOPE (niscope) ---

def _niscope_entries() -> list[dict[str, Any]]:
    pkg = "niscope"
    pfx = "niScope"
    imp = _ni_sess(pkg)
    e = _ni_session_lifecycle(pfx, pkg)

    e.append(_entry(
        f"{pfx} Configure Vertical.vi",
        "{session}.channels[{channel}].configure_vertical("
        "range={range}, coupling={coupling}, "
        "offset={offset})",
        "Configures vertical settings.",
        [_SI,
         _t("channel", None, "input", "channel"),
         _t("range", None, "input", "range"),
         _t("coupling", None, "input", "coupling"),
         _t("offset", None, "input", "offset"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Configure Horizontal Timing.vi",
        "{session}.configure_horizontal_timing("
        "min_sample_rate={min_sample_rate}, "
        "min_num_pts={min_num_pts}, "
        "ref_position={ref_position}, "
        "num_records={num_records}, "
        "enforce_realtime={enforce_realtime})",
        "Configures horizontal timing.",
        [_SI,
         _t("min sample rate", None, "input",
            "min_sample_rate"),
         _t("min record length", None, "input",
            "min_num_pts"),
         _t("reference position", None, "input",
            "ref_position"),
         _t("number of records", None, "input",
            "num_records"),
         _t("enforce realtime", None, "input",
            "enforce_realtime"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Configure Trigger Edge.vi",
        "{session}.configure_trigger_edge("
        "trigger_source={trigger_source}, "
        "level={level}, slope={slope})",
        "Configures edge trigger.",
        [_SI,
         _t("trigger source", None, "input", "trigger_source"),
         _t("level", None, "input", "level"),
         _t("slope", None, "input", "slope"),
         _SO], imp, _SR,
    ))
    for trig_type, method, extras in [
        ("Digital", "configure_trigger_digital",
         [_t("trigger source", None, "input",
             "trigger_source"),
          _t("slope", None, "input", "slope")]),
        ("Software", "configure_trigger_software", []),
        ("Immediate", "configure_trigger_immediate", []),
    ]:
        params = ", ".join(
            f"{t['python_param']}={{{t['python_param']}}}"
            for t in extras
        )
        e.append(_entry(
            f"{pfx} Configure Trigger {trig_type}.vi",
            f"{{session}}.{method}({params})",
            f"Configures {trig_type.lower()} trigger.",
            [_SI] + extras + [_SO], imp, _SR,
        ))
    e.append(_entry(
        f"{pfx} Fetch.vi",
        "{waveforms} = "
        "{session}.channels[{channel}].fetch("
        "num_samples={num_samples})",
        "Fetches acquired waveforms.",
        [_SI,
         _t("channel", None, "input", "channel"),
         _t("num samples", None, "input", "num_samples"),
         _t("timeout", None, "input", "timeout"),
         _SO,
         _t("waveforms", None, "output", "waveforms")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Read.vi",
        "{waveforms} = "
        "{session}.channels[{channel}].read("
        "num_samples={num_samples})",
        "Acquires and reads waveforms.",
        [_SI,
         _t("channel", None, "input", "channel"),
         _t("num samples", None, "input", "num_samples"),
         _t("timeout", None, "input", "timeout"),
         _SO,
         _t("waveforms", None, "output", "waveforms")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Auto Setup.vi",
        "{session}.auto_setup()",
        "Automatically configures the instrument.",
        [_SI, _SO], imp, _SR,
    ))

    return e


# --- NI-FGEN (nifgen) ---

def _nifgen_entries() -> list[dict[str, Any]]:
    pkg = "nifgen"
    pfx = "niFgen"
    imp = _ni_sess(pkg)
    e = _ni_session_lifecycle(pfx, pkg)

    e.append(_entry(
        f"{pfx} Configure Standard Waveform.vi",
        "{session}.configure_standard_waveform("
        "waveform={waveform}, amplitude={amplitude}, "
        "frequency={frequency}, "
        "dc_offset={dc_offset})",
        "Configures standard waveform output.",
        [_SI,
         _t("waveform", None, "input", "waveform"),
         _t("amplitude", None, "input", "amplitude"),
         _t("frequency", None, "input", "frequency"),
         _t("dc offset", None, "input", "dc_offset"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Configure Arb Waveform.vi",
        "{session}.configure_arb_waveform("
        "waveform_handle={waveform_handle}, "
        "gain={gain}, offset={offset})",
        "Configures arbitrary waveform output.",
        [_SI,
         _t("waveform handle", None, "input",
            "waveform_handle"),
         _t("gain", None, "input", "gain"),
         _t("offset", None, "input", "offset"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Create Waveform.vi",
        "{waveform_handle} = "
        "{session}.create_waveform_numpy("
        "{waveform_data})",
        "Creates an arb waveform from data.",
        [_SI,
         _t("waveform data", None, "input", "waveform_data"),
         _SO,
         _t("waveform handle", None, "output",
            "waveform_handle")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Write Waveform.vi",
        "{session}.write_waveform("
        "{waveform_handle}, {waveform_data})",
        "Writes data to an arb waveform.",
        [_SI,
         _t("waveform handle", None, "input",
            "waveform_handle"),
         _t("waveform data", None, "input", "waveform_data"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Initiate Generation.vi",
        "{session}.initiate()",
        "Initiates waveform generation.",
        [_SI, _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Abort Generation.vi",
        "{session}.abort()",
        "Aborts waveform generation.",
        [_SI, _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Is Done.vi",
        "{is_done} = {session}.is_done()",
        "Checks if generation is done.",
        [_SI, _SO,
         _t("done", None, "output", "is_done")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Write Script.vi",
        "{session}.write_script({script})",
        "Writes a generation script.",
        [_SI,
         _t("script", None, "input", "script"),
         _SO], imp, _SR,
    ))

    return e


# --- NI-DCPower (nidcpower) ---

def _nidcpower_entries() -> list[dict[str, Any]]:
    pkg = "nidcpower"
    pfx = "NI-DCPower"
    imp = _ni_sess(pkg)
    e = _ni_session_lifecycle(pfx, pkg)

    e.append(_entry(
        f"{pfx} Configure Voltage Level.vi",
        "{session}.voltage_level = {voltage_level}",
        "Sets the output voltage level.",
        [_SI,
         _t("voltage level", None, "input", "voltage_level"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Configure Current Limit.vi",
        "{session}.current_limit = {current_limit}",
        "Sets the output current limit.",
        [_SI,
         _t("current limit", None, "input", "current_limit"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Configure Current Level.vi",
        "{session}.current_level = {current_level}",
        "Sets the output current level.",
        [_SI,
         _t("current level", None, "input", "current_level"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Configure Voltage Limit.vi",
        "{session}.voltage_limit = {voltage_limit}",
        "Sets the output voltage limit.",
        [_SI,
         _t("voltage limit", None, "input", "voltage_limit"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Configure Output Enabled.vi",
        "{session}.output_enabled = {output_enabled}",
        "Enables or disables the output.",
        [_SI,
         _t("output enabled", None, "input", "output_enabled"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Configure Aperture Time.vi",
        "{session}.configure_aperture_time("
        "{aperture_time})",
        "Configures measurement aperture time.",
        [_SI,
         _t("aperture time", None, "input", "aperture_time"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Measure.vi",
        ("{voltage}, {current}, {in_compliance}"
         " = {session}.measure()"),
        "Performs a single measurement.",
        [_SI, _SO,
         _t("voltage", None, "output", "voltage"),
         _t("current", None, "output", "current"),
         _t("in compliance", None, "output",
            "in_compliance")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Fetch Multiple.vi",
        "{measurements} = {session}.fetch_multiple("
        "count={count}, timeout={timeout})",
        "Fetches multiple measurements.",
        [_SI,
         _t("count", None, "input", "count"),
         _t("timeout", None, "input", "timeout"),
         _SO,
         _t("measurements", None, "output", "measurements")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Query In Compliance.vi",
        "{in_compliance} = "
        "{session}.query_in_compliance()",
        "Queries if output is in compliance.",
        [_SI, _SO,
         _t("in compliance", None, "output",
            "in_compliance")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Query Output State.vi",
        "{output_state} = "
        "{session}.query_output_state()",
        "Queries the output state.",
        [_SI, _SO,
         _t("output state", None, "output", "output_state")],
        imp, _SR,
    ))

    return e


# --- NI-DMM (nidmm) ---

def _nidmm_entries() -> list[dict[str, Any]]:
    pkg = "nidmm"
    pfx = "NI-DMM"
    imp = _ni_sess(pkg)
    e = _ni_session_lifecycle(pfx, pkg)

    e.append(_entry(
        f"{pfx} Configure Measurement.vi",
        "{session}.configure_measurement_digits("
        "measurement_function={function}, "
        "range={range}, "
        "resolution_digits={resolution_digits})",
        "Configures measurement type and range.",
        [_SI,
         _t("function", None, "input", "function"),
         _t("range", None, "input", "range"),
         _t("resolution digits", None, "input",
            "resolution_digits"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Read.vi",
        "{reading} = {session}.read("
        "maximum_time={max_time})",
        "Initiates and reads a measurement.",
        [_SI,
         _t("maximum time", None, "input", "max_time"),
         _SO,
         _t("reading", None, "output", "reading")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Fetch.vi",
        "{reading} = {session}.fetch("
        "maximum_time={max_time})",
        "Fetches a measurement result.",
        [_SI,
         _t("maximum time", None, "input", "max_time"),
         _SO,
         _t("reading", None, "output", "reading")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Configure Multi-Point.vi",
        "{session}.configure_multi_point("
        "trigger_count={trigger_count}, "
        "sample_count={sample_count})",
        "Configures multi-point acquisition.",
        [_SI,
         _t("trigger count", None, "input", "trigger_count"),
         _t("sample count", None, "input", "sample_count"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Configure Trigger.vi",
        "{session}.configure_trigger("
        "trigger_source={trigger_source})",
        "Configures trigger source.",
        [_SI,
         _t("trigger source", None, "input", "trigger_source"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Fetch Multi-Point.vi",
        "{readings} = {session}.fetch_multi_point("
        "array_size={array_size}, "
        "maximum_time={max_time})",
        "Fetches multiple measurement results.",
        [_SI,
         _t("array size", None, "input", "array_size"),
         _t("maximum time", None, "input", "max_time"),
         _SO,
         _t("readings", None, "output", "readings")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Read Multi-Point.vi",
        "{readings} = {session}.read_multi_point("
        "array_size={array_size}, "
        "maximum_time={max_time})",
        "Initiates and reads multiple measurements.",
        [_SI,
         _t("array size", None, "input", "array_size"),
         _t("maximum time", None, "input", "max_time"),
         _SO,
         _t("readings", None, "output", "readings")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Configure Waveform Acquisition.vi",
        "{session}.configure_waveform_acquisition("
        "measurement_function={function}, "
        "range={range}, rate={rate}, "
        "waveform_points={waveform_points})",
        "Configures waveform acquisition.",
        [_SI,
         _t("function", None, "input", "function"),
         _t("range", None, "input", "range"),
         _t("rate", None, "input", "rate"),
         _t("waveform points", None, "input",
            "waveform_points"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Fetch Waveform.vi",
        "{waveform} = {session}.fetch_waveform("
        "array_size={array_size})",
        "Fetches waveform data.",
        [_SI,
         _t("array size", None, "input", "array_size"),
         _SO,
         _t("waveform", None, "output", "waveform")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Read Status.vi",
        ("{backlog}, {status}"
         " = {session}.read_status()"),
        "Reads acquisition status.",
        [_SI, _SO,
         _t("acquisition backlog", None, "output", "backlog"),
         _t("acquisition status", None, "output", "status")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Send Software Trigger.vi",
        "{session}.send_software_trigger()",
        "Sends a software trigger.",
        [_SI, _SO], imp, _SR,
    ))

    return e


# --- NI-SWITCH (niswitch) ---

def _niswitch_entries() -> list[dict[str, Any]]:
    pkg = "niswitch"
    pfx = "niSwitch"
    imp = _ni_sess(pkg)
    e: list[dict[str, Any]] = []

    # Custom init with topology
    e.append(_entry(
        f"{pfx} Initialize With Topology.vi",
        f"{{session_out}} = {pkg}.Session("
        "{resource_name}, topology={topology})",
        "Opens a switch session with topology.",
        [_t("resource name", None, "input", "resource_name"),
         _t("topology", None, "input", "topology"),
         _SO], imp,
    ))
    e.append(_entry(
        f"{pfx} Close.vi",
        "{session}.close()",
        "Closes a switch session.",
        [_SI], imp,
    ))
    e.append(_entry(
        f"{pfx} Connect Channels.vi",
        "{session}.connect({channel1}, {channel2})",
        "Connects two switch channels.",
        [_SI,
         _t("channel 1", None, "input", "channel1"),
         _t("channel 2", None, "input", "channel2"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Disconnect Channels.vi",
        "{session}.disconnect({channel1}, {channel2})",
        "Disconnects two switch channels.",
        [_SI,
         _t("channel 1", None, "input", "channel1"),
         _t("channel 2", None, "input", "channel2"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Disconnect All Channels.vi",
        "{session}.disconnect_all()",
        "Disconnects all channels.",
        [_SI, _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Can Connect.vi",
        "{path_capability} = "
        "{session}.can_connect({channel1}, {channel2})",
        "Checks if channels can be connected.",
        [_SI,
         _t("channel 1", None, "input", "channel1"),
         _t("channel 2", None, "input", "channel2"),
         _SO,
         _t("path capability", None, "output",
            "path_capability")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Get Path.vi",
        "{path} = "
        "{session}.get_path({channel1}, {channel2})",
        "Gets the path between channels.",
        [_SI,
         _t("channel 1", None, "input", "channel1"),
         _t("channel 2", None, "input", "channel2"),
         _SO,
         _t("path", None, "output", "path")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Set Path.vi",
        "{session}.set_path({path_list})",
        "Sets a switch path.",
        [_SI,
         _t("path list", None, "input", "path_list"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Wait For Debounce.vi",
        "{session}.wait_for_debounce("
        "maximum_time_ms={max_time})",
        "Waits for switches to settle.",
        [_SI,
         _t("maximum time", None, "input", "max_time"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Wait For Scan Complete.vi",
        "{session}.wait_for_scan_complete("
        "maximum_time_ms={max_time})",
        "Waits for scanning to complete.",
        [_SI,
         _t("maximum time", None, "input", "max_time"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Get Relay Count.vi",
        "{count} = "
        "{session}.get_relay_count({relay_name})",
        "Gets the relay cycle count.",
        [_SI,
         _t("relay name", None, "input", "relay_name"),
         _SO,
         _t("count", None, "output", "count")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Get Relay Position.vi",
        "{position} = "
        "{session}.get_relay_position({relay_name})",
        "Gets relay position.",
        [_SI,
         _t("relay name", None, "input", "relay_name"),
         _SO,
         _t("position", None, "output", "position")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Relay Control.vi",
        "{session}.relay_control("
        "{relay_name}, {relay_action})",
        "Controls a relay directly.",
        [_SI,
         _t("relay name", None, "input", "relay_name"),
         _t("relay action", None, "input", "relay_action"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Reset.vi",
        "{session}.reset()",
        "Resets the switch.",
        [_SI, _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Self-Test.vi",
        "{result} = {session}.self_test()",
        "Runs self-test.",
        [_SI, _SO,
         _t("result", None, "output", "result")],
        imp, _SR,
    ))

    return e


# --- NI-Digital (nidigital) ---

def _nidigital_entries() -> list[dict[str, Any]]:
    pkg = "nidigital"
    pfx = "niDigital"
    imp = _ni_sess(pkg)
    e = _ni_session_lifecycle(pfx, pkg)

    e.append(_entry(
        f"{pfx} Load Pin Map.vi",
        "{session}.load_pin_map({file_path})",
        "Loads a pin map file.",
        [_SI,
         _t("pin map file path", None, "input", "file_path"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Load Pattern.vi",
        "{session}.load_pattern({file_path})",
        "Loads a digital pattern file.",
        [_SI,
         _t("pattern file path", None, "input", "file_path"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Load Specifications.vi",
        "{session}"
        ".load_specifications_levels_and_timing("
        "{specifications_file_paths}, "
        "{levels_file_paths}, "
        "{timing_file_paths})",
        "Loads specs, levels, and timing files.",
        [_SI,
         _t("specifications file paths", None, "input",
            "specifications_file_paths"),
         _t("levels file paths", None, "input",
            "levels_file_paths"),
         _t("timing file paths", None, "input",
            "timing_file_paths"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Apply Levels And Timing.vi",
        "{session}.apply_levels_and_timing("
        "levels_sheet={levels_sheet}, "
        "timing_sheet={timing_sheet})",
        "Applies levels and timing sheets.",
        [_SI,
         _t("levels sheet", None, "input", "levels_sheet"),
         _t("timing sheet", None, "input", "timing_sheet"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Configure Voltage Levels.vi",
        "{session}.pins[{pin_list}]"
        ".configure_voltage_levels("
        "vil={vil}, vih={vih}, vol={vol}, "
        "voh={voh}, vterm={vterm})",
        "Configures pin voltage levels.",
        [_SI,
         _t("pin list", None, "input", "pin_list"),
         _t("VIL", None, "input", "vil"),
         _t("VIH", None, "input", "vih"),
         _t("VOL", None, "input", "vol"),
         _t("VOH", None, "input", "voh"),
         _t("VTERM", None, "input", "vterm"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Burst Pattern.vi",
        "{site_pass_fail} = {session}.burst_pattern("
        "start_label={start_label})",
        "Bursts a digital pattern.",
        [_SI,
         _t("start label", None, "input", "start_label"),
         _SO,
         _t("site pass/fail", None, "output",
            "site_pass_fail")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Get Site Pass/Fail.vi",
        "{site_pass_fail} = "
        "{session}.get_site_pass_fail()",
        "Gets per-site pass/fail results.",
        [_SI, _SO,
         _t("site pass/fail", None, "output",
            "site_pass_fail")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Get Fail Count.vi",
        "{fail_count} = {session}.get_fail_count()",
        "Gets per-pin fail counts.",
        [_SI, _SO,
         _t("fail count", None, "output", "fail_count")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Wait Until Done.vi",
        "{session}.wait_until_done(timeout={timeout})",
        "Waits for pattern to complete.",
        [_SI,
         _t("timeout", None, "input", "timeout"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Is Done.vi",
        "{is_done} = {session}.is_done()",
        "Checks if pattern burst is done.",
        [_SI, _SO,
         _t("done", None, "output", "is_done")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} PPMU Measure.vi",
        "{measurements} = "
        "{session}.pins[{pin_list}].ppmu_measure("
        "{measurement_type})",
        "Performs PPMU measurement.",
        [_SI,
         _t("pin list", None, "input", "pin_list"),
         _t("measurement type", None, "input",
            "measurement_type"),
         _SO,
         _t("measurements", None, "output", "measurements")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} PPMU Source.vi",
        "{session}.pins[{pin_list}].ppmu_source("
        "{output_function})",
        "Sources voltage or current via PPMU.",
        [_SI,
         _t("pin list", None, "input", "pin_list"),
         _t("output function", None, "input",
            "output_function"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Fetch Capture Waveform.vi",
        "{waveform_data} = "
        "{session}.fetch_capture_waveform("
        "waveform_name={waveform_name}, "
        "samples_to_read={samples_to_read})",
        "Fetches captured waveform data.",
        [_SI,
         _t("waveform name", None, "input", "waveform_name"),
         _t("samples to read", None, "input",
            "samples_to_read"),
         _SO,
         _t("waveform data", None, "output",
            "waveform_data")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Clock Generator Generate Clock.vi",
        "{session}.clock_generator_generate_clock("
        "frequency={frequency})",
        "Generates a clock signal.",
        [_SI,
         _t("frequency", None, "input", "frequency"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Clock Generator Abort.vi",
        "{session}.clock_generator_abort()",
        "Aborts clock generation.",
        [_SI, _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Read Static.vi",
        "{pin_states} = {session}.read_static()",
        "Reads static pin states.",
        [_SI, _SO,
         _t("pin states", None, "output", "pin_states")],
        imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} Write Static.vi",
        "{session}.write_static({state})",
        "Writes static pin states.",
        [_SI,
         _t("state", None, "input", "state"),
         _SO], imp, _SR,
    ))
    e.append(_entry(
        f"{pfx} TDR.vi",
        "{offsets} = {session}.tdr()",
        "Performs TDR calibration.",
        [_SI, _SO,
         _t("offsets", None, "output", "offsets")],
        imp, _SR,
    ))

    return e


# =============================================================
# Main: generate all driver files
# =============================================================

DRIVERS: list[tuple[str, str, Any]] = [
    ("DAQmx", "daqmx.json", _daqmx_entries),
    ("VISA", "visa.json", _visa_entries),
    ("Serial", "serial.json", _serial_entries),
    ("NI-SCOPE", "niscope.json", _niscope_entries),
    ("NI-FGEN", "nifgen.json", _nifgen_entries),
    ("NI-DCPower", "nidcpower.json", _nidcpower_entries),
    ("NI-DMM", "nidmm.json", _nidmm_entries),
    ("NI-SWITCH", "niswitch.json", _niswitch_entries),
    ("NI-Digital", "nidigital.json", _nidigital_entries),
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate driver data JSON for lvkit.",
    )
    parser.add_argument(
        "--output", "-o",
        default="data/drivers",
        help="Output directory (default: data/drivers)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    categories: dict[str, str] = {}
    total = 0

    for cat_name, filename, gen_fn in DRIVERS:
        entries = gen_fn()
        data = _cat(cat_name, entries)
        path = output_dir / filename
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        categories[cat_name] = filename
        total += len(entries)
        print(f"  {cat_name}: {path} ({len(entries)} entries)")

    index = {
        "version": "1.0",
        "source": "generated by scripts/generate_driver_data.py",
        "categories": categories,
    }
    index_path = output_dir / "_index.json"
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)

    print(f"\nTotal: {total} driver mappings across "
          f"{len(DRIVERS)} categories")


if __name__ == "__main__":
    main()
