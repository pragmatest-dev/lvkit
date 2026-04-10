#!/usr/bin/env python3
"""
Populate vi.lib data from NI LabVIEW API Reference PDF.

This script parses the NI LabVIEW API Reference PDF to extract VI and function
documentation, including terminal information, and outputs it to category-based
JSON files in data/vilib/.

Usage:
    python scripts/populate_vilib.py

Dependencies:
    pip install pymupdf
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # pymupdf


@dataclass
class Terminal:
    """A terminal (input or output) of a VI or function."""

    name: str
    direction: str  # "in" or "out"
    type: str | None = None
    description: str = ""
    default_value: str | None = None
    enum_values: list[tuple[int, str]] | None = None
    python_param: str | None = None  # Suggested Python parameter name


@dataclass
class VIEntry:
    """A VI or function entry extracted from the documentation."""

    name: str
    page: int
    category: str
    description: str = ""
    terminals: list[Terminal] = field(default_factory=list)
    vi_path: str | None = None  # e.g., "vi.lib/Utility/sysdir.llb/..."
    is_polymorphic: bool = False
    python_hint: str | None = None
    status: str = "needs_review"  # needs_review, needs_terminals, complete


# Categories and their page ranges (approximate)
CATEGORY_MAP = {
    "Structures": (329, 387),
    "Array": (388, 460),
    "Cluster": (461, 485),
    "Variant": (486, 560),
    "Numeric": (561, 645),
    "String": (646, 780),
    "Boolean": (781, 800),
    "Comparison": (801, 850),
    "File I/O": (1436, 1820),
    "Path": (1490, 1510),
    "Dialog": (1770, 1820),
    "Timing": (350, 360),
    "Application Control": (1820, 2000),
    "Error Handling": (2000, 2100),
}


def extract_page_text(doc: fitz.Document, page_num: int) -> str:
    """Extract text from a page (0-indexed)."""
    if 0 <= page_num < doc.page_count:
        return doc[page_num].get_text()
    return ""


def parse_terminals_from_text(text: str, vi_name: str) -> list[Terminal]:
    """Parse terminal info from 'Inputs/Outputs' section of a page."""
    terminals = []

    # Find the VI's section in the text
    vi_pos = text.find(vi_name)
    if vi_pos == -1:
        return terminals

    # Look for Inputs/Outputs after the VI name
    search_start = vi_pos
    io_pos = text.find("Inputs/Outputs", search_start)
    if io_pos == -1:
        return terminals

    # Extract text from Inputs/Outputs to end of section
    # Section ends at: another VI definition (repeated name) or page boundary
    io_text = text[io_pos + len("Inputs/Outputs"):]

    # Find the next VI/function definition (pattern: "Name\nName\n" at start of line)
    # This indicates a new function started
    next_vi_match = re.search(r"\n([A-Z][a-zA-Z0-9 /&\-]+)\s*\n\1\s*\n", io_text)
    if next_vi_match:
        io_text = io_text[: next_vi_match.start()]

    # Also stop at page footers, but don't stop too early
    # Only cut at footer if we have substantial content already
    for boundary in ["\n© National Instruments"]:
        bound_pos = io_text.find(boundary)
        if bound_pos > 500:  # Only cut if we have >500 chars already
            io_text = io_text[:bound_pos]
            break

    # Split by bullet points (• character)
    bullet_sections = re.split(r"\n•\s*", io_text)

    for section in bullet_sections:
        if not section.strip():
            continue

        # Skip if this looks like a new function definition (repeated title pattern)
        if re.match(r"^([A-Z][a-z]+(?:\s+[A-Z]?[a-z]+)*)\s*\n\1", section):
            break

        # Parse terminal: first line is name with direction indicator
        lines = section.strip().split("\n")
        if not lines:
            continue

        first_line = lines[0].strip()

        # Skip if first line is just a bullet or empty
        if len(first_line) < 2:
            continue

        # Extract terminal name (usually before " — ")
        name_match = re.match(r"\s*([^—]+?)(?:\s*—|$)", first_line)
        if name_match:
            name = name_match.group(1).strip()
            # Clean up formatting artifacts
            name = re.sub(r"^\s*\n?\s*", "", name)
            # Remove any leading/trailing whitespace and special chars
            name = name.strip()
        else:
            continue

        # Skip invalid names
        if not name or len(name) < 2 or name in ["•", "-", "—"]:
            continue

        # Extract description (after " — ")
        desc_match = re.search(r"—\s*(.+)", section, re.DOTALL)
        description = desc_match.group(1).strip() if desc_match else ""

        # Clean up description
        description = re.sub(r"\s+", " ", description)

        # Determine direction based on keywords in name or description
        direction = "in"

        # Output indicators in name
        output_keywords = ["returns", "result", "output", "is the", "contains the"]
        if any(kw in name.lower() for kw in output_keywords):
            direction = "out"
        # Output indicators in description start (first 100 chars)
        elif any(
            kw in description.lower()[:100]
            for kw in ["returns", "is the resulting", "is the output"]
        ):
            direction = "out"

        # Try to determine type from description
        terminal_type = infer_type_from_description(description, name)

        # Look for enum values (numbered list in description)
        enum_values = extract_enum_values(description)

        # Extract default value if mentioned
        default_match = re.search(
            r"(?:default|The default is)\s+(?:is\s+)?([^.]+)", description, re.I
        )
        default_value = default_match.group(1).strip() if default_match else None

        terminal = Terminal(
            name=name,
            direction=direction,
            type=terminal_type,
            description=description[:500],  # Truncate long descriptions
            default_value=default_value,
            enum_values=enum_values if enum_values else None,
            python_param=to_python_name(name),
        )
        terminals.append(terminal)

    return terminals


def infer_type_from_description(description: str, name: str) -> str | None:
    """Infer the type of a terminal from its description and name."""
    desc_lower = description.lower()
    name_lower = name.lower()

    # Path types
    if "path" in name_lower or "path" in desc_lower[:100]:
        return "Path"

    # Boolean types
    if (
        name_lower.endswith("?")
        or "boolean" in desc_lower
        or "TRUE" in description
        or "FALSE" in description
    ):
        return "Boolean"

    # String types
    if "string" in desc_lower[:50]:
        return "String"

    # Numeric/enum with numbered values
    if re.search(r"\b\d+\s*[—–-]\s*\w+", description):
        return "Enum"

    # I32/numeric
    if any(kw in desc_lower for kw in ["integer", "i32", "number", "count", "index"]):
        return "I32"

    # Array types
    if "array" in desc_lower[:50]:
        return "Array"

    # Error cluster
    if "error" in name_lower and "cluster" in desc_lower:
        return "Error Cluster"

    return None


def extract_enum_values(description: str) -> list[tuple[int, str]] | None:
    """Extract enum values from numbered list in description."""
    # Pattern: number followed by text, like "0 User Home—..."
    enum_pattern = r"(\d+)\s+([A-Za-z][^—\n]+?)(?=—|\n\d|\Z)"
    matches = re.findall(enum_pattern, description)

    if len(matches) >= 2:  # At least 2 values to be considered an enum
        results = []
        for num, name in matches:
            val = int(num)
            # Skip if this looks like a page number (>1000) or year
            if val > 100:
                continue
            # Skip if name is too long (likely a full sentence, not an enum name)
            name = name.strip()
            if len(name) > 50:
                continue
            results.append((val, name))
        return results if len(results) >= 2 else None
    return None


def to_python_name(name: str) -> str:
    """Convert terminal name to Python parameter name."""
    # Remove question marks, parentheses
    name = re.sub(r"[?()\[\]]", "", name)
    # Replace spaces and special chars with underscores
    name = re.sub(r"[^a-zA-Z0-9]+", "_", name)
    # Convert to snake_case
    name = name.lower().strip("_")
    return name


def get_category_for_page(page: int) -> str:
    """Determine category based on page number."""
    for category, (start, end) in CATEGORY_MAP.items():
        if start <= page <= end:
            return category
    return "Other"


def parse_toc_for_vis(doc: fitz.Document) -> list[tuple[str, int, int]]:
    """Extract VI/function names from table of contents."""
    toc = doc.get_toc()
    entries = []

    for level, title, page in toc:
        # Skip high-level section headers
        if level <= 1:
            continue

        # Skip non-function entries
        skip_keywords = [
            "example", "overview", "constant",
            "palette", "format code", "considerations",
        ]
        if any(kw in title.lower() for kw in skip_keywords):
            continue

        # Skip section headers (usually have generic names)
        section_keywords = ["functions", "vis", "operations", "overview", "polymorphic"]
        if title.lower() in section_keywords:
            continue

        # Include entries that:
        # 1. End with "Function" or "VI"
        # 2. Contain "Function" or "VI"
        # 3. Are at depth level 4+ (deep entries are usually individual functions)
        # 4. Have a capital + lowercase (proper noun style, like "Get System Directory")
        is_function_vi = (
            title.endswith("Function")
            or title.endswith("VI")
            or " VI" in title
            or "Function" in title
        )

        is_deep_entry = level >= 4

        # Check for proper-noun style name (capital + lowercase, at least 2 words)
        has_proper_name = (
            bool(re.match(r"^[A-Z][a-z]", title)) and len(title.split()) >= 2
        )

        if is_function_vi or (is_deep_entry and has_proper_name):
            entries.append((title, page, level))

    return entries


def parse_vi_entry(doc: fitz.Document, name: str, page: int) -> VIEntry:
    """Parse a single VI/function entry from the PDF."""
    # Get text from this page and the next 2 pages (content often spans 3+ pages)
    text = extract_page_text(doc, page - 1)  # Convert to 0-indexed
    text += "\n" + extract_page_text(doc, page)
    text += "\n" + extract_page_text(doc, page + 1)

    # Extract description (text after the name heading, before Inputs/Outputs)
    desc_match = re.search(
        rf"{re.escape(name)}\s*\n\s*{re.escape(name)}\s*\n(.+?)(?=Inputs/Outputs|\Z)",
        text,
        re.DOTALL,
    )
    description = ""
    if desc_match:
        description = desc_match.group(1).strip()
        description = re.sub(r"\s+", " ", description)[:500]

    # Parse terminals
    terminals = parse_terminals_from_text(text, name)

    # Determine category
    category = get_category_for_page(page)

    # Try to infer vi_path for vi.lib functions
    vi_path = None
    if "VI" in name:
        # Common vi.lib paths
        if "System Directory" in name:
            vi_path = "vi.lib/Utility/sysdir.llb/Get System Directory.vi"
        elif "File Dialog" in name:
            vi_path = "vi.lib/Utility/libraryn.llb/File Dialog.vi"
        elif "Application Directory" in name:
            vi_path = "vi.lib/Utility/sysinfo.llb/Application Directory.vi"

    return VIEntry(
        name=name,
        page=page,
        category=category,
        description=description,
        terminals=terminals,
        vi_path=vi_path,
        status="needs_review" if not terminals else "needs_terminals",
    )


def category_to_filename(category: str) -> str:
    """Convert category name to safe filename."""
    # Replace I/O with io, spaces with dashes, lowercase
    name = category.replace("I/O", "io").replace("/", "-").replace(" ", "-").lower()
    return f"{name}.json"


def organize_by_category(entries: list[VIEntry]) -> dict[str, list[VIEntry]]:
    """Organize entries by category."""
    by_category: dict[str, list[VIEntry]] = {}
    for entry in entries:
        cat = entry.category
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(entry)
    return by_category


def entry_to_dict(entry: VIEntry) -> dict:
    """Convert VIEntry to JSON-serializable dict."""
    return {
        "name": entry.name,
        "page": entry.page,
        "category": entry.category,
        "description": entry.description,
        "vi_path": entry.vi_path,
        "is_polymorphic": entry.is_polymorphic,
        "python_hint": entry.python_hint,
        "status": entry.status,
        "terminals": [
            {
                "name": t.name,
                "direction": t.direction,
                "type": t.type,
                "description": t.description,
                "default_value": t.default_value,
                "enum_values": t.enum_values,
                "python_param": t.python_param,
            }
            for t in entry.terminals
        ],
    }


def main():
    """Main entry point.

    Maintainer-only script. Reads NI's LabVIEW API reference PDF and
    populates lvpy's vilib JSON. The PDF is NOT shipped with lvpy
    (gitignored — not redistributable), so this script expects the
    maintainer to drop it at the path below before running.
    """
    # Paths
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    pdf_path = project_root / "src" / "lvpy" / "data" / "labview-api-ref.pdf"
    output_dir = project_root / "src" / "lvpy" / "data" / "vilib"

    if not pdf_path.exists():
        raise SystemExit(
            f"PDF not found at {pdf_path}.\n"
            "This is a maintainer-only tool that needs NI's LabVIEW API "
            "reference manual. Drop the PDF at the path above and re-run."
        )

    # Create output directory
    output_dir.mkdir(exist_ok=True)

    print(f"Opening PDF: {pdf_path}")
    doc = fitz.open(str(pdf_path))
    print(f"Total pages: {doc.page_count}")

    # Get TOC entries
    print("Parsing table of contents...")
    toc_entries = parse_toc_for_vis(doc)
    print(f"Found {len(toc_entries)} VI/function entries in TOC")

    # Parse each entry
    print("Parsing VI entries...")
    vi_entries = []
    for name, page, level in toc_entries:
        try:
            entry = parse_vi_entry(doc, name, page)
            vi_entries.append(entry)
        except Exception as e:
            print(f"  Error parsing {name}: {e}")

    print(f"Parsed {len(vi_entries)} entries")

    # Organize by category
    by_category = organize_by_category(vi_entries)
    print(f"Categories: {list(by_category.keys())}")

    # Write index file
    index = {
        "version": "1.0",
        "source": "labview-api-ref.pdf",
        "categories": {
            cat: category_to_filename(cat) for cat in by_category.keys()
        },
        "total_entries": len(vi_entries),
    }

    with open(output_dir / "_index.json", "w") as f:
        json.dump(index, f, indent=2)
    print(f"Wrote {output_dir / '_index.json'}")

    # Write category files
    for category, entries in by_category.items():
        filename = category_to_filename(category)
        filepath = output_dir / filename

        data = {
            "category": category,
            "count": len(entries),
            "entries": [entry_to_dict(e) for e in entries],
        }

        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Wrote {filepath} ({len(entries)} entries)")

    # Print summary
    print("\n=== Summary ===")
    for category, entries in sorted(by_category.items()):
        with_terminals = sum(1 for e in entries if e.terminals)
        print(f"  {category}: {len(entries)} entries ({with_terminals} with terminals)")

    doc.close()


if __name__ == "__main__":
    main()
