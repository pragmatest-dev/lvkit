# Tunnel Types

Loops and structures connect to their surroundings via tunnels.

## Shift Registers (lSR/rSR)

Paired tunnels for loop-carried state.

- `lSR` (left): Provides initial value on first iteration
- `rSR` (right): Receives updated value at end of each iteration

```python
# Generated pattern
shift_var = initial_value  # from lSR input
for i in range(n):
    # loop body uses shift_var
    shift_var = new_value  # to rSR
result = shift_var  # rSR output available after loop
```

## Loop Tunnels (lpTun)

Simple pass-through. Direction determined by wire connectivity.

- Input lpTun: Value enters loop, available inside
- Output lpTun: Value exits loop (last iteration or auto-indexed array)

## Accumulators (lMax)

Output-only tunnel for accumulator results (auto-indexed arrays, max values).

## Pairing

`Tunnel.paired_terminal_uid` links lSR↔rSR pairs. Code generators must handle both sides together.
