// Bundles each page into dist/, next to the manifest Cockpit reads
import * as esbuild from "esbuild";
import { mkdirSync, writeFileSync } from "node:fs";

export const pages = [
    { id: "setup", label: "NAS Setup", order: 10 },
    { id: "storage", label: "Storage", order: 11 },
    { id: "shares", label: "Shares", order: 12 },
    { id: "health", label: "Health", order: 13 },
];

mkdirSync("dist", { recursive: true });

const manifest = { version: 0, menu: {} };
for (const page of pages) {
    manifest.menu[page.id] = { label: page.label, path: `${page.id}.html`, order: page.order };
    writeFileSync(`dist/${page.id}.html`, `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${page.label}</title>
  <link rel="stylesheet" href="style.css">
  <script src="../base1/cockpit.js"></script>
</head>
<body>
  <div id="app"></div>
  <script src="${page.id}.js"></script>
</body>
</html>
`);
}
writeFileSync("dist/manifest.json", JSON.stringify(manifest, null, 2) + "\n");

const options = {
    entryPoints: [...pages.map(page => `src/${page.id}.tsx`), "src/style.css"],
    outdir: "dist",
    bundle: true,
    minify: true,
    target: "es2022",
    jsx: "automatic",
    jsxImportSource: "preact",
    logLevel: "info",
};

if (process.argv.includes("--watch")) {
    await (await esbuild.context(options)).watch();
} else {
    await esbuild.build(options);
}
