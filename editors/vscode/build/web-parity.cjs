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
    # Render from the STAGED /proj tree with /proj as the search root, exactly
    # as the shipped web extension does — must match \`lvkit render --format svg
    # --theme light\` on the native side (which reads the same files off disk).
    svg, _ = render_vi_file_titled(Path(p), search_paths=[Path("/proj")])
    return svg or ""
_r`);

  // Report the MINIMAL dependency closure of a staged VI (the /proj paths the
  // loader resolved) so we can stage its callees, one layer at a time.
  const depsOf = pyodide.runPython(`
import json
from pathlib import Path
from lvkit.graph import InMemoryVIGraph
from lvkit.load_mode import LoadMode
def _deps(p):
    g = InMemoryVIGraph()
    n = g.load_vi(Path(p), LoadMode.MINIMAL, search_paths=[Path("/proj")])
    return json.dumps([str(x) for x in g.get_dependency_paths(n)])
_deps`);

  // Mirror a VI's whole dependency closure into the Pyodide FS under /proj,
  // preserving each file's native absolute path (so recorded RELATIVE paths —
  // e.g. a parent class at ../../Layer/Layer.lvclass — resolve), staging one
  // layer per pass until the set stops growing. This is the SAME progressive
  // MINIMAL-load → get_dependency_paths → fetch loop the web extension runs.
  function stageClosure(viNative) {
    const abs = path.resolve(viNative);
    const staged = new Set();
    const put = (nativePath) => {
      const proj = "/proj" + nativePath;
      if (staged.has(proj) || !fs.existsSync(nativePath)) return false;
      pyodide.FS.mkdirTree(path.posix.dirname(proj));
      pyodide.FS.writeFile(proj, fs.readFileSync(nativePath));
      staged.add(proj);
      return true;
    };
    put(abs);
    const entryProj = "/proj" + abs;
    for (let round = 0; round < 20; round++) {
      const deps = JSON.parse(depsOf(entryProj));
      let added = 0;
      for (const dp of deps) {
        if (dp.startsWith("/proj/") && put(dp.slice("/proj".length))) added++;
      }
      if (added === 0) break;
    }
    return entryProj;
  }

  for (const vi of FIXTURES) {
    const entry = stageClosure(vi);
    const svg = render(entry);
    fs.writeFileSync(path.join(OUT, `${slug(vi)}.wasm.svg`), svg);
    console.log(`  wasm rendered ${vi} (${svg.length} bytes)`);
  }
})().catch((e) => {
  console.error("WEB-PARITY (wasm) FAILED:", String(e));
  process.exit(1);
});
