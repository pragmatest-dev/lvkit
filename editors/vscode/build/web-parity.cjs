// CI parity guard (wasm side): render each fixture in Pyodide — booted from the
// SELF-HOSTED media/pyodide + media/wheels, exactly as the shipped web extension
// does — and write the bare SVG so check-web-parity.sh can diff it against the
// native render. Determinism must hold across interpreters (Python 3.14 wasm vs
// native), so any diff is a real regression.
//
//   node web-parity.cjs <out_dir> <fixture.vi> [<fixture.vi> ...]
const { loadPyodide } = require("pyodide"); // resolved from editors/vscode/node_modules
const fs = require("fs");
const path = require("path");

const VSC = path.resolve(__dirname, "..");
const PYODIR = path.join(VSC, "media", "pyodide") + "/";
const WHEELS = path.join(VSC, "media", "wheels");
const OUT = process.argv[2];
const FIXTURES = process.argv.slice(3);
// Same per-character slug as check-web-parity.sh so output filenames line up.
const slug = (p) => p.replace(/[^a-z0-9]/gi, "_");

(async () => {
  const pyodide = await loadPyodide({ indexURL: PYODIR });
  await pyodide.loadPackage(["micropip", "pydantic", "Pillow"]);
  const micropip = pyodide.pyimport("micropip");
  pyodide.FS.mkdirTree("/wheels");
  pyodide.FS.mkdirTree("/vi");
  const whls = fs
    .readdirSync(WHEELS)
    .filter((f) => f.endsWith(".whl"))
    .sort((a, b) => (a.startsWith("lvkit-") ? 1 : -1)); // lvkit last (imports the others)
  for (const w of whls) pyodide.FS.writeFile(`/wheels/${w}`, fs.readFileSync(path.join(WHEELS, w)));
  for (const w of whls) await micropip.install.callKwargs(`emfs:/wheels/${w}`, { deps: false });

  const render = pyodide.runPython(`
import os
os.environ["LVKIT_CACHE_DIR"] = "/tmp/lvkit-parity-cache"
from pathlib import Path
from lvkit.render import render_vi_file_titled
def _r(p):
    # Bare render, default theme — must match \`lvkit render --format svg
    # --theme light\` on the native side.
    svg, _ = render_vi_file_titled(Path(p))
    return svg or ""
_r`);

  for (const vi of FIXTURES) {
    const dst = `/vi/${slug(vi)}.vi`;
    pyodide.FS.writeFile(dst, fs.readFileSync(vi));
    const svg = render(dst);
    fs.writeFileSync(path.join(OUT, `${slug(vi)}.wasm.svg`), svg);
    console.log(`  wasm rendered ${vi} (${svg.length} bytes)`);
  }
})().catch((e) => {
  console.error("WEB-PARITY (wasm) FAILED:", String(e));
  process.exit(1);
});
