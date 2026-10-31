"""Every module the generated code imports by a bare name is a name a generated
variable avoids (``ast_utils.GENERATED_MODULE_NAMES``)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from lvkit.codegen.ast_utils import GENERATED_MODULE_NAMES, to_var_name

_SRC = Path(__file__).resolve().parents[1] / "src" / "lvkit"
_BARE_IMPORT = re.compile(r"^\s*import (\w+)", re.MULTILINE)
_QUOTED_IMPORT = re.compile(r"[\"']\s*import (\w+)|\\n(?:import|from) (\w+)")


def _catalog_imports(node: object, found: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "imports" and isinstance(value, list):
                for statement in value:
                    found.update(_BARE_IMPORT.findall(str(statement)))
            _catalog_imports(value, found)
    elif isinstance(node, list):
        for value in node:
            _catalog_imports(value, found)


def test_every_bare_import_the_codegen_writes_is_reserved() -> None:
    found: set[str] = set()
    for path in (_SRC / "data").rglob("*.json"):
        _catalog_imports(json.loads(path.read_text(encoding="utf-8")), found)
    for path in (_SRC / "codegen").rglob("*.json"):
        _catalog_imports(json.loads(path.read_text(encoding="utf-8")), found)
    for path in (_SRC / "codegen").rglob("*.py"):
        for first, second in _QUOTED_IMPORT.findall(path.read_text(encoding="utf-8")):
            found.add(first or second)
    assert found - GENERATED_MODULE_NAMES - {"annotations"} == set()


def test_a_label_named_like_a_module_gets_a_trailing_underscore() -> None:
    assert to_var_name("OS") == "os_"
    assert to_var_name("re") == "re_"
    assert to_var_name("Time") == "time_"
    assert to_var_name("Test") == "test"
