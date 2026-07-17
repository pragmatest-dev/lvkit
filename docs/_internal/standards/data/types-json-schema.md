# _types.json Schema

File `src/lvkit/data/vilib/_types.json` defines typedefs (enums, clusters) referenced by vilib VIs.

## Why It Exists

Clean-room parsing doesn't have access to LabVIEW's enum labels. This file provides meaningful enum member names from NI's PDF documentation instead of raw integers.

## Entry Structure

```json
{
  "sysdir.llb:System Directory Type.ctl": {
    "name": "SystemDirectoryType",
    "kind": "enum",
    "underlying_type": "UInt16",
    "description": "System directory type for Get System Directory.vi",
    "values": {
      "USER_HOME": {"value": 0, "description": "User's home directory"},
      "USER_DESKTOP": {"value": 1, "description": "User's desktop"}
    }
  }
}
```

## Key Format

Keys are qualified typedef names: `<container>:<filename.ctl>`

Example: `sysdir.llb:System Directory Type.ctl`

## Usage

When a vilib terminal has `type: "sysdir.llb:System Directory Type.ctl"`, the resolver looks up the typedef here to get enum values for code generation.
