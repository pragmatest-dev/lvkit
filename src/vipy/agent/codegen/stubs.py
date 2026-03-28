"""Stub file generator for unknown SubVIs.

Generates separate module files with union signatures from all observed usages.
"""

from __future__ import annotations

from pathlib import Path


class StubGenerator:
    """Generates stub module files for unknown SubVIs.

    Collects usages across multiple VIs and generates union signatures.
    All parameters are optional (with defaults) so callers don't need
    updating as more usages are discovered.
    """

    def __init__(self):
        # Accumulate usages: {func_name: {vi_name, usages: [...]}}
        self._stubs: dict[str, dict] = {}

    def add_usages(self, unknown_subvis: dict[str, dict]) -> None:
        """Add usages from a ModuleBuilder.

        Args:
            unknown_subvis: From ModuleBuilder.get_unknown_subvis()
        """
        for func_name, info in unknown_subvis.items():
            if func_name not in self._stubs:
                self._stubs[func_name] = {
                    "vi_name": info["vi_name"],
                    "usages": [],
                }
            self._stubs[func_name]["usages"].extend(info["usages"])

    def generate_all(self, output_dir: Path) -> list[Path]:
        """Generate stub files for all collected unknowns.

        Args:
            output_dir: Directory to write stub files

        Returns:
            List of generated file paths
        """
        generated = []
        for func_name, info in self._stubs.items():
            path = output_dir / f"{func_name}.py"
            code = self._generate_stub_module(func_name, info)
            path.write_text(code)
            generated.append(path)
        return generated

    def _generate_stub_module(self, func_name: str, info: dict) -> str:
        """Generate a single stub module file."""
        vi_name = info["vi_name"]
        usages = info["usages"]

        # Compute union signature
        union_inputs, union_outputs, usage_summary = (
            self._compute_union_signature(usages)
        )

        lines = []
        lines.append(f'"""STUB: {vi_name}')
        lines.append("")
        lines.append("This module was auto-generated from observed usages.")
        lines.append("Implement based on VI name semantics.")
        lines.append("")
        lines.append(f"Observed in {len(usages)} caller(s):")
        for usage_line in usage_summary:
            lines.append(f"  {usage_line}")
        lines.append('"""')
        lines.append("")
        lines.append("from __future__ import annotations")
        lines.append("")
        lines.append("from pathlib import Path")
        lines.append("from typing import Any, NamedTuple")
        lines.append("")

        # Generate result class if there are outputs
        result_class = self._to_class_name(vi_name) + "Result"
        if union_outputs:
            lines.append("")
            lines.append(f"class {result_class}(NamedTuple):")
            for out in union_outputs:
                py_type = self._map_type(out["type"])
                lines.append(f"    {out['name']}: {py_type}")
            lines.append("")

        # Generate function - ALL params optional with defaults
        params = []
        for inp in union_inputs:
            py_type = self._map_type(inp["type"])
            params.append(f"{inp['name']}: {py_type} = None")
        params_str = ", ".join(params)

        return_type = result_class if union_outputs else "None"
        lines.append("")
        lines.append(f"def {func_name}({params_str}) -> {return_type}:")
        lines.append(f'    """STUB: {vi_name}')
        lines.append("")
        lines.append("    TODO: Implement based on VI name semantics.")
        lines.append('    """')
        lines.append(f'    raise NotImplementedError("{vi_name} not yet converted")')
        lines.append("")

        return "\n".join(lines)

    def _compute_union_signature(
        self, usages: list[dict]
    ) -> tuple[list[dict], list[dict], list[str]]:
        """Compute union of inputs/outputs from all usages."""
        # Merge by terminal index
        inputs_by_index: dict[int, dict] = {}
        outputs_by_index: dict[int, dict] = {}

        for usage in usages:
            for inp in usage["inputs"]:
                idx = inp["index"]
                if idx not in inputs_by_index:
                    inputs_by_index[idx] = inp.copy()
                # Prefer named over generic
                if inp["name"] and not inp["name"].startswith("input_"):
                    inputs_by_index[idx]["name"] = inp["name"]

            for out in usage["outputs"]:
                idx = out["index"]
                if idx not in outputs_by_index:
                    outputs_by_index[idx] = out.copy()
                if out["name"] and not out["name"].startswith("output_"):
                    outputs_by_index[idx]["name"] = out["name"]

        # Sort by index
        union_inputs = [inputs_by_index[k] for k in sorted(inputs_by_index.keys())]
        union_outputs = [outputs_by_index[k] for k in sorted(outputs_by_index.keys())]

        # Ensure unique names
        seen = set()
        for inp in union_inputs:
            if inp["name"] in seen or not inp["name"]:
                inp["name"] = f"input_{inp['index']}"
            seen.add(inp["name"])

        seen = set()
        for out in union_outputs:
            if out["name"] in seen or not out["name"]:
                out["name"] = f"output_{out['index']}"
            seen.add(out["name"])

        # Build usage summary
        summary = []
        for usage in usages:
            in_names = [i["name"] for i in usage["inputs"]]
            out_names = [o["name"] for o in usage["outputs"]]
            ins = ', '.join(in_names)
            outs = ', '.join(out_names)
            summary.append(f"{usage['caller']}: ({ins}) -> ({outs})")

        return union_inputs, union_outputs, summary

    def _to_class_name(self, name: str) -> str:
        """Convert VI name to PascalCase class name."""
        name = name.replace(".vi", "").replace(".VI", "")
        if ":" in name:
            name = name.split(":")[-1]
        words = name.replace("-", " ").replace("_", " ").split()
        return "".join(w.capitalize() for w in words) or "VI"

    def _map_type(self, lv_type: str) -> str:
        """Map LabVIEW type to Python type."""
        type_map = {
            "Path": "Path",
            "String": "str",
            "Boolean": "bool",
            "NumInt32": "int",
            "NumInt16": "int",
            "NumFloat64": "float",
            "NumFloat32": "float",
            "Array": "list",
            "Cluster": "dict",
            "Void": "None",
        }
        return type_map.get(lv_type, "Any")
