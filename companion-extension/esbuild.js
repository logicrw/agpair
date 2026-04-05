const esbuild = require("esbuild");

const production = process.argv.includes("--production");

esbuild
  .build({
    entryPoints: ["src/extension.ts"],
    bundle: true,
    outfile: "dist/extension.js",
    external: ["vscode"], // vscode is provided by the host
    format: "cjs",
    platform: "node",
    target: "node18",
    sourcemap: !production,
    minify: production,
    logLevel: "info",
  })
  .catch(() => process.exit(1));
