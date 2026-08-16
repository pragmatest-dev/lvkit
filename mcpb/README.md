# lvkit — Claude Desktop bundle (MCPB source)

`manifest.json` here is the **source** for lvkit's Claude Desktop extension (`.mcpb`). It is
not the shippable bundle — the release pipeline (`.github/workflows/publish-bundles.yml`)
assembles a per-(OS, arch) `.mcpb` from it.

## What CI builds (mac + Windows legs only — Claude Desktop has no Linux)

Per target (`darwin-arm64`, `darwin-x64`, `win32-x64`), reusing the same signed onedir build
that goes into the VSIX / standalone zip / plugin:

```
build/
├── manifest.json     # from here; CI stamps version + compatibility.platforms to this target
└── server/           # the onedir bundle contents → exe at server/lvkit (server/lvkit.exe on win)
```

then `npx @anthropic-ai/mcpb pack build lvkit-<target>.mcpb` and attach it to the release.
Because PyInstaller binaries are arch-specific and MCPB's `entry_point` selects by OS (not
arch), we ship one `.mcpb` per (OS, arch); the user downloads the one for their machine.

## How it runs

`server.type: "binary"` → Claude Desktop launches `${__dirname}/server/lvkit mcp` (auto-`.exe`
on Windows via `platform_overrides`). No Python/uv/pip. **No install-time configuration.** The
project a tool answers for is: `project=<path>` when given, else the client's open workspace
(Claude Code / VS Code launch the server IN the repo, so cwd IS the project), else — on Claude
Desktop, where cwd isn't your VIs — the tool asks once (`_require_vis`: "point me at your VI
folder") and Claude's cross-conversation memory retains the answer. Every tool also takes an
explicit `project` path, and the `list_projects` tool reports what's active.

## Install (end user)

Download `lvkit-<target>.mcpb` from the release / pragmatest.com → **double-click** (or drag
into Claude Desktop, or Settings → Extensions → Install Extension…) — nothing to configure.
The first time you ask about VIs, tell it your project folder; it remembers. macOS binary is
unsigned in v1: if Gatekeeper blocks it, `xattr -dr com.apple.quarantine` the downloaded file.
