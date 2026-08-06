# Handoff: error-indicator names in lvkit-wintest/repo

## Question answered
"Counts of each name this project uses for error indicators."
Defined as: connector-pane terminals with `direction=output` and `is_error_cluster=true`.

## Result — 379 terminals across 487 VIs (case-sensitive tally)

| Count | Name |
|---:|---|
| 351 | `error out` |
| 14 | `source` (NOT a real name — see caveats) |
| 2 | `Error out` |
| 2 | `filtered error details` |
| 2 | `Test Method Error` |
| 1 | `Constructor Error` |
| 1 | `Error Out` |
| 1 | `no error out` |
| 1 | `vi error out` |
| 4 | `control_134`, `control_262`, `control_685`, `control_93` (NOT real names) |

**Interpretation:** the convention is `error out` (~369 of 379 once unresolved
labels are attributed), with 3 case variants and 7 genuinely custom names.
`vi error out` (`source\Utilities\Get VI Error Out Value.vi`) is a *second*
error output alongside a normal `error out`.

## Caveats — 18 rows are unresolved labels, not names

lvkit fabricates a label when a VI's string sections are compressed. Two
fallback shapes, both confirmed from the graph itself (no raw-byte grepping
needed):

1. **`control_<fp_dco_uid>` (4 VIs)** — `Examples\ExampleTestSuite\New.vi`,
   `Examples\ExampleTestSuite\setUp.vi`, `Programmatic API\Run Tests (Project).vi`,
   `Tests\MySecondTestCase\CleanUp.vi`. Tell: in `get_signatures`, *every*
   terminal is `control_NNN`, inputs included — the whole pane failed to
   resolve. Types and `field_names` still resolve correctly.
2. **`source` (14 VIs)** — all of `source\Built Project Integration\VITester_*.vi`.
   The error cluster's own third field name leaks through as the label. Tell:
   the input *and* output error clusters both come back named `source`.
   Cross-check: the `source\LabVIEW Project Plugin\` copies of the same VIs
   (`VITester_Global_Init.vi`, `VITester_Item_Init.vi`) resolve cleanly as
   `error in` / `error out`.

Diagnosing this from the graph is sufficient. Do not grep raw .vi bytes —
those VIs have fully compressed string sections, so a byte search returns 0
hits for both the real and the fabricated name and proves nothing either way.

## Tooling gotchas (will recur on any project-wide query here)

- **Index is fine.** Path-keyed and persisted; `index(refresh=true)` is ~1s,
  reports 487 VIs. Project-wide counts are trustworthy.
- **`find_terminals` overflows the tool-result cap.** 379 rows = 141,508 chars.
  The harness dumps it to a file under the session dir and asks you to read it
  in chunks. **Don't** — parse it out-of-context instead:

  ```powershell
  $d = Get-Content "<dumped-file>" -Raw | ConvertFrom-Json
  $d.result | ForEach-Object { if ($_.terminal) { $_.terminal.name } else { $_.name } } |
    Group-Object -CaseSensitive | Sort-Object Count -Descending | Format-Table Count, Name -AutoSize
  ```

- **`find_terminals` nests the terminal under a `terminal` key** rather than
  flattening it — hence the `if ($_.terminal)` above.
- **`Group-Object` is case-insensitive by default.** Without `-CaseSensitive`
  you will silently fold `Error out` / `Error Out` into `error out` and report
  354 instead of 351.
- **No way to trim the payload.** `find_terminals` takes only
  `project, direction, is_error_cluster, name, py_type` — all row filters, no
  projection / limit / offset / aggregation. Filters are echoed back as
  constant columns on every row, so narrowing rows worsens signal-per-byte
  (measured: ~373 chars/row, ~60 of which vary; `vi_name` duplicates the
  `vi_path` leaf on 379/379 rows). `get_signatures` is worse — it returns full
  `field_names` arrays for every terminal. No CLI fallback: `lvkit.exe` has no
  `find-terminals` subcommand, it's MCP-only.

## Reproduce

```
mcp__lvkit__index(project="C:\Users\ryanf\lvkit-wintest\repo", refresh=true)
mcp__lvkit__find_terminals(project=..., direction="output", is_error_cluster=true)
  -> overflows to file; tally with the PowerShell above
mcp__lvkit__get_signatures(project=..., vi_names=[...])  # to diagnose unresolved labels
```
