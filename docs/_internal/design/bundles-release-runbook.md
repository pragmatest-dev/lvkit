# Release runbook — one build, many bundles (dogfood → publish)

How to validate and ship the multi-bundle install (Claude Desktop `.mcpb`, VS Code
extension, Claude Code plugin, standalone binary) built by `publish-bundles.yml`. Everything
below is a **gated** action — it builds/signs on real runners and/or publishes, so it's yours
to run, not the assistant's.

## What ships from one build

`publish-bundles.yml` builds the `lvkit` binary once per platform (native runner, Windows PE
signed) and packages every form from it:
- `lvkit-<target>.vsix` → VS Code Marketplace
- `lvkit-mcp-<version>-<target>.zip` → standalone binary (any MCP client)
- `lvkit-plugin-<target>.zip` → Claude Code plugin (binary + 7 skills), pointed at by
  `.claude-plugin/marketplace.json`
- `lvkit-<target>.mcpb` → Claude Desktop (macOS + Windows), from `mcpb/manifest.json`

`.sha256` sidecar for every asset. `assemble_bundles.sh`/`.ps1` do the packaging.

## Step 1 — Dry run (build everything, publish nothing)

Run the workflow via **workflow_dispatch** (Actions → "Publish lvkit bundles" → Run). The
`publish` (Marketplace) and `release-bundles` (Release assets) jobs are gated to `ext-v*` tag
pushes, so a dispatch run only **builds + uploads artifacts**. This is the first real test of
the Windows `.ps1` and of `mcpb pack` on native mac/Windows runners (neither is testable on
Linux/WSL locally). Download the `binary-<target>` artifacts and eyeball the zip/.mcpb layout.

## Step 2 — Dogfood each channel from the dry-run artifacts (no publish needed)

- **Claude Desktop:** double-click the downloaded `lvkit-<target>.mcpb` → it installs; pick a
  real VI folder in the settings UI; confirm the tools answer on a real VI. (No URL needed —
  the file installs directly.)
- **VS Code:** `code --install-extension lvkit-<target>.vsix`; open a `.vi` (View), right-click
  a changed `.vi` (Open Visual Diff); confirm MCP auto-registers in agent mode (trusted
  workspace, VS Code ≥ 1.101).
- **Claude Code plugin:** add a **local** marketplace and install from the downloaded archive,
  e.g. unzip `lvkit-plugin-<target>.zip` into a dir with a local `marketplace.json` whose
  `source` is a relative path, then `claude plugin marketplace add <that dir>` +
  `claude plugin install`. Confirm the MCP tools + a skill load with no uv/pip present.
- **Dev configs:** unzip `lvkit-mcp-<version>-<target>.zip`; wire VS Code `.vscode/mcp.json` /
  Codex `.codex/config.toml` / Copilot `~/.copilot/mcp-config.json` at the absolute binary
  path; `lvkit mcp --selftest` exits 0.
- **Integrity:** check each `.sha256`. **macOS:** confirm `xattr -dr com.apple.quarantine`
  clears Gatekeeper.

## Step 3 — Publish (only after every channel passes)

1. Bump `editors/vscode/package.json` `version` (the extension track).
2. Push tag `ext-v<that version>`. That triggers the full run: VSIX → Marketplace, all bundles
   → the GitHub Release. `.claude-plugin/marketplace.json` uses `releases/latest/download/…`,
   so the plugin goes live the moment the (non-prerelease) release publishes.
3. Merge this branch's docs (`install.md`, `vscode-extension.md`, …) so the site describes the
   now-live paths.

## Known risks / follow-ups

- **`.ps1` and `mcpb pack` are unvalidated on Linux** — the dry run is their first real test;
  expect to iterate once.
- **MCPB `server.type:"binary"`** is the less-common path (Node is the documented default) —
  verify the Desktop install end-to-end.
- **Per-OS plugin `name` stamping**: CI stamps `plugin.json` `name` → `lvkit-<target>` to match
  the marketplace entry; confirm Claude Code accepts it.
- **sha256 in `marketplace.json`**: not pinned yet (uses `latest/download`); pinning needs a
  CI commit-back — deferred.
- **macOS notarization** deferred (v1 = unsigned + `xattr`).
