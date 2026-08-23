// Bundle the WEB (browser / web-worker extension host) entry.
// The desktop entry (extension.js) is plain CommonJS run by Node and needs no
// bundling; the web entry must be a single browser-target CJS module.
const esbuild = require("esbuild");

esbuild
  .build({
    entryPoints: ["web/extension.js"],
    bundle: true,
    format: "cjs",
    platform: "browser",
    target: "es2020",
    outfile: "dist/web/extension.js",
    external: ["vscode"], // provided by the extension host at runtime
    sourcemap: false,
    minify: false,
  })
  .then(() => console.log("built dist/web/extension.js"))
  .catch((e) => {
    console.error(e);
    process.exit(1);
  });
