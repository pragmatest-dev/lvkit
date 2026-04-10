#!/usr/bin/env python3
"""Debug dataflow for loop VIs."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lvpy.agent.codegen.context import CodeGenContext
from lvpy.memory_graph import InMemoryVIGraph

# Load a VI with loops
vi_path = Path(
    "samples/OpenG/extracted/File Group 0/user.lib/_OpenG.lib"
    "/file/file.llb/Create Dir if Non-Existant__ogtk.vi"
)
if not vi_path.exists():
    # Try relative to script
    vi_path = Path(__file__).parent.parent / vi_path

if not vi_path.exists():
    print(f"VI not found: {vi_path}")
    sys.exit(1)

graph = InMemoryVIGraph()
graph.load_vi(vi_path)

vi_name = list(graph.list_vis())[0]
print(f"VI: {vi_name}")

ctx = graph.get_vi_context(vi_name)

print("\n=== INPUTS ===")
for inp in ctx["inputs"]:
    print(f"  {inp['id']}: {inp.get('name')}")

print("\n=== CONSTANTS ===")
for c in ctx["constants"]:
    print(f"  {c['id']}: {c.get('value')} ({c.get('type')})")

print("\n=== OPERATIONS ===")
for op in ctx["operations"]:
    print(f"  {op['id']}: {op.get('name')} {op.get('labels')}")
    if "Loop" in op.get("labels", []):
        print(f"    loop_type: {op.get('loop_type')}")
        print("    tunnels:")
        for t in op.get("tunnels", []):
            print(
                f"      {t['tunnel_type']}: outer={t['outer_terminal_uid']}"
                f" -> inner={t['inner_terminal_uid']}"
            )
        print(f"    inner_nodes: {len(op.get('inner_nodes', []))} nodes")
        for inner in op.get("inner_nodes", []):
            print(f"      - {inner['id']}: {inner.get('name')} {inner.get('labels')}")
            for term in inner.get("terminals", []):
                print(
                    f"        {term.get('direction')}: {term['id']}"
                    f" (idx={term.get('index')})"
                )

print("\n=== DATA FLOW (all) ===")
for flow in ctx["data_flow"]:
    print(f"  {flow['from_terminal_id']} -> {flow['to_terminal_id']}")

# Check flow map entries
codegen_ctx = CodeGenContext.from_vi_context(ctx)

print("\n=== FLOW MAP (first 20 entries) ===")
for dest_id, src_info in list(codegen_ctx._flow_map.items())[:20]:
    parent = src_info.get("src_parent_name", "unknown")
    print(f"  {dest_id} <- {src_info['src_terminal']} (parent: {parent})")

# Check tunnel inner terminals and what flows to them
print("\n=== TUNNEL INFO ===")
for op in ctx["operations"]:
    if "Loop" in op.get("labels", []):
        print(f"Loop: {op['id']} ({op.get('loop_type')})")
        for t in op.get("tunnels", []):
            print(
                f"  {t['tunnel_type']}: outer={t['outer_terminal_uid']}"
                f" inner={t['inner_terminal_uid']}"
            )
            # Check what flows TO the inner terminal
            for flow in ctx["data_flow"]:
                if flow['to_terminal_id'] == t['inner_terminal_uid']:
                    print(f"    -> TO inner: {flow['from_terminal_id']}")
                if flow['from_terminal_id'] == t['inner_terminal_uid']:
                    print(f"    -> FROM inner: {flow['to_terminal_id']}")
