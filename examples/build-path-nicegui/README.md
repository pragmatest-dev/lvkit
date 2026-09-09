# Build Path — VI → Python + NiceGUI (vertical slice)

A proof that a full LabVIEW VI — front panel **and** block diagram — can become
runnable, all-Python native code, with the UI as a thin generated binding layer
over pure logic.

Source VI: OpenG `Build Path (traditional)` (`file.llb/Build Path__ogtk.vi`,
scalar instance).

- **`logic.py`** — the block diagram, produced by lvkit's deterministic
  graph→Python pass. Pure Python; imports nothing from the UI. This is the
  deterministic-oracle output you validate/train an AI author against.
- **`panel.py`** — the front panel, derived from the VI's connector pane: each
  typed input picks its widget, widgets bind to a plain dataclass, and the Run
  handler calls `logic.build_path` off the event loop via `run.io_bound`. This
  is the shape the NiceGUI generator will emit from any function-style VI's pane
  (hand-assembled here to prove the vertical).
- **`app.py`** — mounts the panel and serves it.

## Run

```bash
uv run --with nicegui python examples/build-path-nicegui/app.py
# open http://localhost:8080
```

`nicegui` is pulled ephemerally by `uv run --with` — it is **not** a lvkit
dependency.
