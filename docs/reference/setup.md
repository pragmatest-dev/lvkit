# setup

Create the project-local `.lvkit/` resolution store, and optionally install AI
editor skills so an agent (Claude Code, Copilot) can help resolve unknown
primitives and vi.lib VIs as it works.

## Synopsis

```bash
lvkit setup [directory] [{claude,copilot,all}] [options]
```

## Arguments

| Argument | Description |
|----------|-------------|
| `directory` | Directory in which to create `.lvkit/`. Must already exist — `setup` does not create it. Defaults to the current directory. |
| `{claude,copilot,all}` | Which AI agent to install skills for. `all` installs both the Claude Code and Copilot skill sets. Omit to auto-detect from project layout: `CLAUDE.md` / `.claude/` for Claude Code, `.github/copilot-instructions.md` / `.github/instructions/` / `.github/agents.md` for Copilot. |

## Options

| Option | Description |
|--------|-------------|
| `--no-skills` | Create the `.lvkit/` resolution store and README without installing any AI editor skills. Use this if you want to add primitive or vi.lib mappings manually. |
| `--force` | Overwrite existing skill files even if they have local edits. |

`setup` does not take the [SubVI resolution flags](subvi-resolution.md) —
`--vilib`/`--project-root` etc. — itself; later
[`generate`](generate.md)/[`render`](render.md) runs read them from the store
this command creates.

## Example

```bash
lvkit setup
```

```text
Initialized .lvkit/ store at /home/you/project/.lvkit
Installed 5 Claude Code skill(s):
  /home/you/project/.claude/skills/lvkit-describe/SKILL.md
  /home/you/project/.claude/skills/lvkit-convert/SKILL.md
  /home/you/project/.claude/skills/lvkit-resolve-primitive/SKILL.md
  /home/you/project/.claude/skills/lvkit-resolve-vilib/SKILL.md
  /home/you/project/.claude/skills/lvkit-idiomatic/SKILL.md
```

This auto-detects the project's AI agent from its layout, creates `.lvkit/` in
the current directory, and installs the matching editor skills. Re-running
with nothing changed prints "Claude Code skills already up to date." instead
of rewriting the files. If auto-detection finds neither a Claude Code marker
nor a Copilot marker, `setup` still creates `.lvkit/`, prints "No AI agent
detected...", and exits `0` with no skills installed.

To target a specific directory and agent explicitly:

```bash
lvkit setup . claude
```

## Notes

- `.lvkit/` holds a `README.md` (the license-boundary explainer — lvkit never
  reads `.lvkit/` back into its own shipped `data/`) plus `vilib/`, `openg/`,
  and `drivers/` subdirectories, each with an empty `_index.json`. `setup`
  creates these empty. `generate`/`render` cache resolved vi.lib terminal
  layouts into `.lvkit/vilib/` as they run (see
  [SubVI & vi.lib resolution](subvi-resolution.md)); `.lvkit/primitives.json`
  doesn't exist until something writes it — by hand, or via the
  `lvkit-resolve-primitive` skill this command can install.
- `setup` is safe to re-run: an existing `.lvkit/` store and its README are
  left untouched, and skill files that already match the installed template
  are skipped rather than treated as conflicts.
- Without `--force`, if any target skill file already exists with local edits,
  `setup` writes no skill files and exits with an error listing every
  conflict. Re-run with `--force` to overwrite them — use it when
  reinstalling after a skill update.
- Claude Code skills install one file per skill to
  `.claude/skills/<name>/SKILL.md`: `lvkit-describe`, `lvkit-convert`,
  `lvkit-resolve-primitive`, `lvkit-resolve-vilib`, `lvkit-idiomatic`. Copilot
  gets the same five workflows as `.github/prompts/<name>.prompt.md` slash
  commands, plus a single `.github/instructions/lvkit.instructions.md` router
  that auto-loads into every chat and suggests the right one.
- Neither a local LabVIEW install nor a prior [`detect`](detect.md) run is
  required — `setup` creates `.lvkit/` empty regardless. `.lvkit/vilib/` fills
  in later, as `generate`/`render` resolve vi.lib VIs against whatever
  `--vilib` points at (see [SubVI & vi.lib resolution](subvi-resolution.md)).

## See also

- [generate](generate.md) — the command whose unresolved primitives/vi.lib VIs the installed skills help fix.
- [render](render.md) — also reads resolution flags from the `.lvkit/` store this command creates.
- [SubVI & vi.lib resolution](subvi-resolution.md) — the `--vilib`/`--project-root` flags that read from the store `setup` creates.
- [detect](detect.md) — find the local LabVIEW install that `.lvkit/vilib/` caches against.
