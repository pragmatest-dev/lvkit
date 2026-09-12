# Build Path — VI → Python + NiceGUI (vertical slice)

A proof that a full LabVIEW VI — front panel **and** block diagram — can become
runnable, all-Python native code, with the UI as a thin generated binding layer
over pure logic.

Source VI: OpenG `Build Path (traditional)` (`file.llb/Build Path__ogtk.vi`,
scalar instance).

- **`logic.py`** — the block diagram, produced by lvkit's deterministic
  graph→Python pass. Pure Python; imports nothing from the UI. This is the
  deterministic-oracle output you validate/train an AI author against.
- **`panel.py`** — the front panel, built from the same control library the
  generator uses: a bindable `State`, the VI toolbar + `RunController`, and
  `path_control` (an outlined path field with a real server-filesystem Browse).
  Run latches the inputs, calls `logic.build_path` off the event loop via
  `run.io_bound`, and writes the appended path. Hand-assembled here to prove the
  vertical; the generator emits the same building blocks as a `<vi>_panel.py`.
- **`app.py`** — single-sources the control runtime (aliased as `controls`),
  mounts the panel, and serves it.

## Run

```bash
uv run --with nicegui python examples/build-path-nicegui/app.py
# open http://localhost:8080
```

`nicegui` is pulled ephemerally by `uv run --with` — it is **not** a lvkit
dependency.
