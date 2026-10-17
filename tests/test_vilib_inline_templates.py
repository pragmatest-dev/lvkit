"""An inline vi.lib template names its terminals as ``{param}``.

``in_0`` / ``out_1`` is the primitive template language; in an inline vi.lib
template it would reach the generated module as an undefined name.
"""

from __future__ import annotations

import json
import re

from lvkit._data import data_dir as _data_dir

VILIB_DIR = _data_dir() / "vilib"


def _inline_templates():
    for path in sorted(VILIB_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data.get("entries", []):
            if entry.get("inline") and entry.get("python_code"):
                yield path.name, entry


def test_inline_templates_do_not_use_primitive_input_names():
    offenders = [
        (file, entry["name"])
        for file, entry in _inline_templates()
        if re.search(r"\b(?:in|out)_\d+\b", entry["python_code"])
    ]
    assert offenders == []


def test_trim_whitespace_template_trims_by_location():
    (entry,) = (e for _file, e in _inline_templates() if e["name"] == "Trim Whitespace")
    code = (
        entry["python_code"]
        .replace("{trimmed_string}", "result")
        .replace("{string}", "text")
        .replace("{location_both}", "location")
    )
    text = " \t ab \r\n"
    expected = {0: "ab", 1: "ab \r\n", 2: " \t ab"}
    for location, trimmed in expected.items():
        namespace = {"text": text, "location": location}
        exec(code, namespace)  # noqa: S102
        assert namespace["result"] == trimmed
