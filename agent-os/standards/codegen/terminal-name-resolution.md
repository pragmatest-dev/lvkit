# Terminal Name Resolution

Terminal names resolve differently for vilib VIs vs project SubVIs.

## vilib VIs (LabVIEW standard library)

Call sites to vilib VIs lack terminal names (clean-room parsing). Names come from `data/vilib/*.json` which contains NI documentation.

```python
# vilib lookup by terminal index
if vilib_inputs and term_index in vilib_inputs:
    param_name = to_var_name(vilib_inputs[term_index])
```

vilib is only checked for known vilib VIs. Project SubVIs won't be found there.

## Project SubVIs

For non-vilib SubVIs, resolution priority:

1. **callee_param_name** - From connector pane mapping
2. **terminal.name** - From node itself
3. **vi_context_lookup** - Fallback to callee VI's front panel

```python
if callee_param:
    param_name = to_var_name(callee_param)
elif term_name:
    param_name = to_var_name(term_name)
else:
    callee_name = ctx.get_callee_param_name(subvi_name, term_index)
```

## VILib Auto-Update

When caller wires vilib terminals not yet in JSON, `auto_update_terminals()` adds them during code generation.
