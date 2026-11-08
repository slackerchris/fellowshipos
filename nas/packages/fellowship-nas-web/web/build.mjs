// Bundles the desktop into dist/, which the server serves as it is
import * as esbuild from "esbuild";
import { mkdirSync, writeFileSync } from "node:fs";

mkdirSync("dist", { recursive: true });

writeFileSync("dist/index.html", `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark light">
  <title>FellowshipOS NAS</title>
  <link rel="icon" href="mark.svg" type="image/svg+xml">
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <div id="app"></div>
  <script src="main.js"></script>
</body>
</html>
`);

writeFileSync("dist/mark.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">
<rect x="2" y="2" width="44" height="44" rx="11" fill="#1b2923"/>
<path d="M8 35l9-13 5 7 6-11 12 17z" fill="#29392f" stroke="#efe7d5" stroke-width="1.8" stroke-linejoin="round"/>
<path d="M34 10l1.1 2.4 2.4 1.1-2.4 1.1L34 17l-1.1-2.4-2.4-1.1 2.4-1.1z" fill="#c5a465"/>
</svg>
`);

// Misty mountains, drawn the same way every build from a fixed seed

function random(seed) {
    return () => {
        seed = (seed + 0x6d2b79f5) | 0;
        let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
}

const W = 1920;
const H = 1080;

/** A ridge line by midpoint displacement: rough, with a few tall peaks. */
function ridge(next, base, height, roughness = 0.55) {
    const count = 129;
    const ys = new Array(count).fill(0);
    ys[0] = base - next() * height * 0.5;
    ys[count - 1] = base - next() * height * 0.5;
    let step = count - 1;
    let spread = height;
    while (step > 1) {
        for (let i = step / 2; i < count; i += step) {
            ys[i] = (ys[i - step / 2] + ys[i + step / 2]) / 2 - (next() - 0.35) * spread;
        }
        step /= 2;
        spread *= roughness;
    }
    return ys.map((y, i) => [Math.round((i * W) / (count - 1)), Math.round(Math.min(y, base + height * 0.2))]);
}

function fill(points) {
    return `M0 ${H}L${points.map(([x, y]) => `${x} ${y}`).join("L")}L${W} ${H}Z`;
}

/** Pines standing on a ridge, as narrow triangles */
function pines(next, points, tallest) {
    let path = "";
    for (let x = 4; x < W; x += 9 + next() * 16) {
        const i = Math.min(points.length - 2, Math.floor((x / W) * (points.length - 1)));
        const [x0, y0] = points[i];
        const [x1, y1] = points[i + 1];
        const y = y0 + ((x - x0) / (x1 - x0)) * (y1 - y0) + 3;
        const h = tallest * (0.35 + next() * 0.65);
        const w = h * 0.22;
        path += `M${(x - w).toFixed(1)} ${y.toFixed(1)}L${x.toFixed(1)} ${(y - h).toFixed(1)}L${(x + w).toFixed(1)} ${y.toFixed(1)}Z`;
    }
    return path;
}

function wallpaper(theme) {
    const next = random(1954);
    const layers = [
        { base: 610, height: 330, roughness: 0.58 },
        { base: 720, height: 240, roughness: 0.55 },
        { base: 840, height: 170, roughness: 0.5, trees: 22 },
        { base: 965, height: 95, roughness: 0.48, trees: 40 },
    ].map(layer => {
        const points = ridge(next, layer.base, layer.height, layer.roughness);
        return { ...layer, points, trees: layer.trees ? pines(next, points, layer.trees) : "" };
    });
    const stars = Array.from({ length: 70 }, () => [next() * W, next() * 420, 0.4 + next() * 1.1, 0.15 + next() * 0.45]);

    const t = theme === "dark" ? {
        sky: ["#0a1311", "#14231e", "#1e302a"], glow: "#c5a465", glowOpacity: 0.17,
        moon: "#efe7d5", moonOpacity: 0.5, mist: "#efe7d5", mistOpacity: [0.07, 0.06, 0.05, 0.05],
        ridges: ["#1f312a", "#192921", "#13201a", "#0d1613"],
    } : {
        sky: ["#efe8d6", "#e6e3d2", "#d5d9c8"], glow: "#c5a465", glowOpacity: 0.3,
        moon: "#fbf6e6", moonOpacity: 0.9, mist: "#fbf8f0", mistOpacity: [0.55, 0.5, 0.45, 0.4],
        ridges: ["#b7c1b0", "#97a796", "#778b7c", "#586d60"],
    };

    const mist = layers.map((layer, i) => {
        const top = layer.base - layer.height * 0.55;
        return `<rect y="${Math.round(top)}" width="${W}" height="${Math.round(layer.height * 1.1)}" fill="url(#mist${i})"/>`;
    });
    const mistGradients = layers.map((_, i) => `<linearGradient id="mist${i}" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="${t.mist}" stop-opacity="0"/>
<stop offset="0.55" stop-color="${t.mist}" stop-opacity="${t.mistOpacity[i]}"/>
<stop offset="1" stop-color="${t.mist}" stop-opacity="0"/>
</linearGradient>`);

    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid slice">
<defs>
<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="${t.sky[0]}"/><stop offset="0.55" stop-color="${t.sky[1]}"/><stop offset="1" stop-color="${t.sky[2]}"/>
</linearGradient>
<radialGradient id="glow" cx="1390" cy="300" r="520" gradientUnits="userSpaceOnUse">
<stop offset="0" stop-color="${t.glow}" stop-opacity="${t.glowOpacity}"/><stop offset="1" stop-color="${t.glow}" stop-opacity="0"/>
</radialGradient>
${mistGradients.join("\n")}
</defs>
<rect width="${W}" height="${H}" fill="url(#sky)"/>
<rect width="${W}" height="${H}" fill="url(#glow)"/>
${theme === "dark" ? stars.map(([x, y, r, o]) => `<circle cx="${x.toFixed(0)}" cy="${y.toFixed(0)}" r="${r.toFixed(1)}" fill="#efe7d5" opacity="${o.toFixed(2)}"/>`).join("") : ""}
<circle cx="1390" cy="300" r="30" fill="${t.moon}" opacity="${t.moonOpacity}"/>
${layers.map((layer, i) => `<path d="${fill(layer.points)}${layer.trees}" fill="${t.ridges[i]}"/>\n${mist[i]}`).join("\n")}
</svg>
`;
}

writeFileSync("dist/wallpaper-dark.svg", wallpaper("dark"));
writeFileSync("dist/wallpaper-light.svg", wallpaper("light"));

const options = {
    entryPoints: ["src/main.tsx", "src/style.css"],
    outdir: "dist",
    bundle: true,
    minify: true,
    target: "es2022",
    jsx: "automatic",
    jsxImportSource: "preact",
    // The wallpapers sit next to the stylesheet; leave their addresses alone
    external: ["*.svg"],
    logLevel: "info",
};

if (process.argv.includes("--watch")) {
    await (await esbuild.context(options)).watch();
} else {
    await esbuild.build(options);
}
