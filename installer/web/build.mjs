// Bundles the install page into dist/. It shares the desktop's stylesheet
// and wallpapers, so the NAS looks the same from install to desktop.
import * as esbuild from "esbuild";
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";

const desktop = "../../nas/packages/fellowship-nas-web/web";

mkdirSync("dist", { recursive: true });
writeFileSync("dist/index.html", `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark light">
  <title>Install FellowshipOS</title>
  <link rel="stylesheet" href="main.css">
</head>
<body>
  <div id="app"></div>
  <script src="main.js"></script>
</body>
</html>
`);
copyFileSync(`${desktop}/wallpapers/dark.jpg`, "dist/wallpaper-dark.jpg");
copyFileSync(`${desktop}/wallpapers/light.jpg`, "dist/wallpaper-light.jpg");

const options = {
    entryPoints: ["src/main.tsx"],
    outdir: "dist",
    bundle: true,
    minify: true,
    target: "es2022",
    jsx: "automatic",
    jsxImportSource: "preact",
    // The desktop's stylesheet asks for its drawn fallback wallpapers; the
    // installer only ships the photos
    external: ["*.svg", "*.jpg"],
    logLevel: "info",
};

if (process.argv.includes("--watch")) {
    await (await esbuild.context(options)).watch();
} else {
    await esbuild.build(options);
}
