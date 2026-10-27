# setup

Create the project-local `.lvkit/` resolution store, and optionally install AI
editor skills so an agent (Claude Code, GitHub Copilot, or OpenAI Codex) can
help resolve unknown primitives and vi.lib VIs as it works.

## Synopsis

```bash
lvkit setup [directory] [{claude,copilot,codex,all}] [options]
```

## Arguments

| Argument | Description |
|----------|-------------|
| `directory` | Directory in which to create `.lvkit/`. Must already exist — `setup` does not create it. Defaults to the current directory. |
| `{claude,copilot,codex,all}` | Which AI agent to install skills for. `all` installs Claude Code, Copilot, and Codex skill sets. Omit to auto-detect from project layout: `CLAUDE.md` / `.claude/` for Claude Code, `.github/copilot-instructions.md` / `.github/instructions/` / `.github/skills/` / `.github/prompts/` / `.github/agents.md` for Copilot, and `AGENTS.md` / `AGENTS.override.md` / `.agents/` / `.codex/` for Codex. |

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
Installed 7 Claude Code skill(s):
  /home/you/project/.claude/skills/lvkit/SKILL.md
  /home/you/project/.claude/skills/lvkit-describe/SKILL.md
  /home/you/project/.claude/skills/lvkit-query/SKILL.md
  /home/you/project/.claude/skills/lvkit-convert/SKILL.md
  /home/you/project/.claude/skills/lvkit-document/SKILL.md
  /home/you/project/.claude/skills/lvkit-review/SKILL.md
  /home/you/project/.claude/skills/lvkit-resolve/SKILL.md
```

This auto-detects the project's AI agent from its layout, creates `.lvkit/` in
the current directory, and installs the matching editor skills. Re-running
with nothing changed prints "Claude Code skills already up to date." instead
of rewriting the files. If auto-detection finds no Claude Code, Copilot, or
Codex marker, `setup` still creates `.lvkit/`, prints "No AI agent detected...",
and exits `0` with no skills installed.

To target a specific directory and agent explicitly:

```bash
lvkit setup . claude
```

For the ChatGPT desktop app, Codex CLI, or the Codex IDE extension:

```bash
lvkit setup codex
```

This installs the same seven workflows under
`.agents/skills/<name>/SKILL.md`. ChatGPT and Codex can activate them
implicitly from their descriptions. In the ChatGPT desktop app, type `@` to
select a skill. In Codex CLI or the IDE extension, use `$lvkit-describe`,
`$lvkit-convert`, or another installed skill name. `setup` does not create
or modify `AGENTS.md` or `.codex/config.toml`.

## Notes

- `.lvkit/` holds a `README.md` (the license-boundary explainer — lvkit never
  reads `.lvkit/` back into its own shipped `data/`) plus `vilib/`, `openg/`,
  and `drivers/` subdirectories, each with an empty `_index.json`. `setup`
  creates these empty. `generate`/`render` cache resolved vi.lib terminal
  layouts into `.lvkit/vilib/` as they run (see
  [SubVI & vi.lib resolution](subvi-resolution.md)); `.lvkit/primitives.json`
  doesn't exist until something writes it — by hand, or via the
  `lvkit-resolve` skill this command can install.
- `setup` is safe to re-run: an existing `.lvkit/` store and its README are
  left untouched, and skill files that already match the installed template
  are skipped rather than treated as conflicts.
- Without `--force`, if any target skill file already exists with local edits,
  `setup` writes no skill files and exits with an error listing every
  conflict. Re-run with `--force` to overwrite them — use it when
  reinstalling after a skill update.
- Claude Code skills install one file per skill to
  `.claude/skills/<name>/SKILL.md`: `lvkit`, `lvkit-describe`, `lvkit-query`,
  `lvkit-convert`, `lvkit-document`, `lvkit-review`, `lvkit-resolve`. Copilot
  gets the same seven workflows as Agent Skills in
  `.github/skills/<name>/SKILL.md`; VS Code loads them by description and
  also offers each as a `/<name>` slash command. `setup copilot` deletes
  `.github/prompts/lvkit*.prompt.md` files and
  `.github/instructions/lvkit.instructions.md` when they carry the
  lvkit-managed marker, and leaves files you wrote or edited alone. Codex gets one
  Agent Skill per workflow in `.agents/skills/<name>/SKILL.md`; its native
  skill discovery provides both automatic and explicit invocation.
- Neither a local LabVIEW install nor a prior [`detect`](detect.md) run is
  required — `setup` creates `.lvkit/` empty regardless. `.lvkit/vilib/` fills
  in later, as `generate`/`render` resolve vi.lib VIs against whatever
  `--vilib` points at (see [SubVI & vi.lib resolution](subvi-resolution.md)).

## See also

- [generate](generate.md) — the command whose unresolved primitives/vi.lib VIs the installed skills help fix.
- [render](render.md) — also reads resolution flags from the `.lvkit/` store this command creates.
- [SubVI & vi.lib resolution](subvi-resolution.md) — the `--vilib`/`--project-root` flags that read from the store `setup` creates.
- [detect](detect.md) — find the local LabVIEW install that `.lvkit/vilib/` caches against.
