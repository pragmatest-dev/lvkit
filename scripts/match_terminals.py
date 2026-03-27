#!/usr/bin/env python3
"""Terminal matching tool for correlating observed indices to vilib names.

Shows observed terminal indices alongside vilib terminal names and helps
identify which index corresponds to which terminal name.

Usage:
    python scripts/match_terminals.py [vi_name]

If vi_name is provided, shows details for that VI only.
Otherwise shows all pending observations.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load_pending_observations() -> dict:
    """Load pending terminal observations."""
    pending_file = Path("data/vilib/_pending_terminals.json")
    if not pending_file.exists():
        return {"observations": {}}
    with open(pending_file) as f:
        return json.load(f)


def load_vilib_entries() -> dict[str, dict]:
    """Load all vilib entries indexed by VI name."""
    vilib_dir = Path("data/vilib")
    entries = {}
    for json_file in vilib_dir.glob("*.json"):
        if json_file.name.startswith("_"):
            continue
        with open(json_file) as f:
            data = json.load(f)
        for entry in data.get("entries", []):
            name = entry.get("name", "")
            # Index by both with and without .vi extension
            entries[name] = entry
            entries[f"{name}.vi"] = entry
    return entries


def get_vilib_file_for_entry(vi_name: str) -> Path | None:
    """Find which vilib JSON file contains an entry."""
    vilib_dir = Path("data/vilib")
    for json_file in vilib_dir.glob("*.json"):
        if json_file.name.startswith("_"):
            continue
        with open(json_file) as f:
            data = json.load(f)
        for entry in data.get("entries", []):
            if entry.get("name") == vi_name or f"{entry.get('name')}.vi" == vi_name:
                return json_file
    return None


def analyze_vi(vi_name: str, obs_data: dict, vilib_entries: dict) -> None:
    """Analyze a single VI's terminal observations vs vilib definitions."""
    print(f"\n{'='*60}")
    print(f"VI: {vi_name}")
    print(f"{'='*60}")

    # Get vilib entry
    vilib_entry = (
        vilib_entries.get(vi_name) or vilib_entries.get(vi_name.replace(".vi", ""))
    )
    vilib_terminals = vilib_entry.get("terminals", []) if vilib_entry else []

    # Get observations
    terminal_map = obs_data.get("terminal_map", {})

    # Separate by direction
    obs_inputs = [
        (int(idx), info) for idx, info in terminal_map.items()
        if info["direction"] == "input"
    ]
    obs_outputs = [
        (int(idx), info) for idx, info in terminal_map.items()
        if info["direction"] == "output"
    ]
    obs_inputs.sort()
    obs_outputs.sort()

    vilib_inputs = [t for t in vilib_terminals if t.get("direction") == "in"]
    vilib_outputs = [t for t in vilib_terminals if t.get("direction") == "out"]

    # Show observed terminals
    print("\n--- OBSERVED TERMINALS (from caller dataflow) ---")
    print("\nInputs:")
    for idx, info in obs_inputs:
        types = ", ".join(info.get("observed_types", []))
        names = ", ".join(info.get("observed_names", [])) or "(no name)"
        count = info.get("count", 0)
        print(f"  [{idx:2d}] {types:20s} | names: {names} | seen {count}x")

    print("\nOutputs:")
    for idx, info in obs_outputs:
        types = ", ".join(info.get("observed_types", []))
        names = ", ".join(info.get("observed_names", [])) or "(no name)"
        count = info.get("count", 0)
        print(f"  [{idx:2d}] {types:20s} | names: {names} | seen {count}x")

    # Show vilib terminals
    print("\n--- VILIB TERMINALS (from documentation) ---")
    print("\nInputs:")
    for t in vilib_inputs:
        name = t.get("name", "?")
        typ = t.get("type") or "?"
        idx = t.get("index")
        idx_str = f"[{idx:2d}]" if idx is not None else "[??]"
        print(f"  {idx_str} {name:40s} | type: {typ}")

    print("\nOutputs:")
    for t in vilib_outputs:
        name = t.get("name", "?")
        typ = t.get("type") or "?"
        idx = t.get("index")
        idx_str = f"[{idx:2d}]" if idx is not None else "[??]"
        print(f"  {idx_str} {name:40s} | type: {typ}")

    # Show matching suggestions
    print("\n--- MATCHING SUGGESTIONS ---")

    # Check for unmatched
    vilib_no_index = [t for t in vilib_terminals if t.get("index") is None]
    if vilib_no_index:
        print("\nTerminals needing index assignment:")
        for t in vilib_no_index:
            name = t.get("name")
            direction = t.get("direction")
            typ = t.get("type") or "?"

            # Find candidate indices
            if direction == "in":
                candidates = [idx for idx, info in obs_inputs]
            else:
                candidates = [idx for idx, info in obs_outputs]

            # Filter out already-assigned indices
            assigned = {
                t.get("index") for t in vilib_terminals if t.get("index") is not None
            }
            candidates = [c for c in candidates if c not in assigned]

            print(f"  '{name}' ({direction}, {typ})")
            print(f"    Candidate indices: {candidates}")

    # Check for direction mismatches
    print("\nDirection verification:")
    for t in vilib_terminals:
        idx = t.get("index")
        if idx is None:
            continue
        name = t.get("name")
        vilib_dir = t.get("direction")

        obs_info = terminal_map.get(str(idx))
        if obs_info:
            obs_dir = obs_info.get("direction")
            obs_dir_mapped = "in" if obs_dir == "input" else "out"
            if obs_dir_mapped != vilib_dir:
                print(
                    f"  MISMATCH [{idx}] '{name}':"
                    f" vilib={vilib_dir}, observed={obs_dir}"
                )
            else:
                print(f"  OK [{idx}] '{name}': {vilib_dir}")

    # Show vilib file location
    vilib_file = get_vilib_file_for_entry(vi_name)
    if vilib_file:
        print(f"\nVilib file: {vilib_file}")


def main():
    pending = load_pending_observations()
    vilib_entries = load_vilib_entries()

    observations = pending.get("observations", {})

    if not observations:
        print("No pending observations found.")
        return

    # Filter by VI name if provided
    if len(sys.argv) > 1:
        vi_filter = sys.argv[1]
        observations = {
            k: v for k, v in observations.items() if vi_filter.lower() in k.lower()
        }

    if not observations:
        print(f"No observations matching '{sys.argv[1]}'")
        return

    print(f"Found {len(observations)} VI(s) with pending observations")

    for vi_name, obs_data in observations.items():
        analyze_vi(vi_name, obs_data, vilib_entries)


if __name__ == "__main__":
    main()
