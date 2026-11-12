// The desktop's themes, read from FellowshipOS's shared theme files
// (themes/*.toml at the top of the repository, installed to
// /usr/share/fellowship/themes). The Rust desktop reads the same files with
// the same rules; see themes/README.md.
//
// Each theme gives four colours per mode (window, panel, text and accent) and
// a wallpaper; the rest is worked out from those. A theme can pin any
// worked-out colour to an exact value. The build stops if a theme's text
// would be hard to read.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

/** Where the theme files are: FELLOWSHIP_THEMES_DIR, or the repository's themes/. */
export const THEMES_DIR = process.env.FELLOWSHIP_THEMES_DIR
    ?? fileURLToPath(new URL("../../../../themes/", import.meta.url));

/** The small part of TOML the theme files use: [tables], key = "string",
    key = number, and # comments. Anything else stops the build. */
export function parseToml(source, file) {
    const root = {};
    let table = root;
    source.split("\n").forEach((raw, i) => {
        const line = raw.replace(/\s+#.*$/, "").replace(/^#.*$/, "").trim();
        if (!line) {
            return;
        }
        const header = line.match(/^\[([A-Za-z0-9_.-]+)\]$/);
        if (header) {
            table = root;
            for (const part of header[1].split(".")) {
                table = table[part] ??= {};
            }
            return;
        }
        const pair = line.match(/^([A-Za-z0-9_-]+)\s*=\s*(?:"([^"\\]*)"|(-?\d+))$/);
        if (!pair) {
            throw new Error(`${file}:${i + 1}: can't read "${raw.trim()}"`);
        }
        table[pair[1]] = pair[2] ?? Number(pair[3]);
    });
    return root;
}

const COLOURS = ["window", "panel", "text", "accent"];

function check(name, theme) {
    for (const mode of ["dark", "light"]) {
        const m = theme[mode];
        if (!m) {
            throw new Error(`${name}: no [${mode}] table`);
        }
        for (const key of [...COLOURS, "wallpaper"]) {
            if (typeof m[key] !== "string") {
                throw new Error(`${name}: [${mode}] needs ${key}`);
            }
        }
        for (const [key, value] of Object.entries({ ...Object.fromEntries(COLOURS.map(k => [k, m[k]])), ...(m.pinned ?? {}) })) {
            if (!/^#[0-9a-fA-F]{6}$/.test(value)) {
                throw new Error(`${name}: [${mode}] ${key} = "${value}" isn't a #RRGGBB colour`);
            }
        }
    }
}

/** Every theme file, by id (its file name). Fellowship first, then in `order`. */
export function loadThemes(dir = THEMES_DIR) {
    const themes = readdirSync(dir)
        .filter(f => f.endsWith(".toml"))
        .map(f => {
            const id = f.slice(0, -5);
            const theme = parseToml(readFileSync(join(dir, f), "utf8"), f);
            check(id, theme);
            for (const mode of ["dark", "light"]) {
                for (const key of COLOURS) {
                    theme[mode][key] = theme[mode][key].toLowerCase();
                }
                theme[mode].pinned = Object.fromEntries(
                    Object.entries(theme[mode].pinned ?? {}).map(([k, v]) => [k, v.toLowerCase()]));
            }
            return [id, { title: theme.name ?? id, order: theme.order ?? 99, ...theme }];
        })
        .sort(([a, x], [b, y]) => (a === "fellowship" ? -1 : b === "fellowship" ? 1 : x.order - y.order || a.localeCompare(b)));
    if (!themes.some(([id]) => id === "fellowship")) {
        throw new Error(`${dir} has no fellowship.toml, the default theme`);
    }
    return Object.fromEntries(themes);
}

export const THEMES = loadThemes();

const rgb = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
const hex = parts => "#" + parts.map(v => Math.round(v).toString(16).padStart(2, "0")).join("");

/** a with share of b mixed in, as CSS's color-mix in sRGB would give */
export const mix = (a, b, share) => hex(rgb(a).map((v, i) => v + (rgb(b)[i] - v) * share));

function luminance(colour) {
    const [r, g, b] = rgb(colour).map(v => {
        const c = v / 255;
        return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(a, b) {
    const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (high + 0.05) / (low + 0.05);
}

/** The colours worked out from a mode's four, before pins. Names without
    the "--" are what a theme's [mode.pinned] table uses. */
function workedOut(theme, mode) {
    const c = theme[mode];
    const dark = theme.dark;
    const light = mode === "light";
    return {
        "desktop": c.window,
        "bg": c.window,
        "surface": c.panel,
        "surface-sunk": light ? mix(c.window, c.text, 0.04) : mix(c.window, c.panel, 0.5),
        "raised": light ? mix(c.window, c.text, 0.09) : mix(c.panel, c.text, 0.09),
        "border": light ? mix(c.window, c.text, 0.22) : mix(c.panel, c.text, 0.22),
        "text": c.text,
        "muted": mix(c.text, c.panel, light ? 0.22 : 0.3),
        "accent": c.accent,
        "accent-text": light ? c.panel : c.window,
        "gold": c.accent,
        "icon-label": c.text,
        // The taskbar is dark in either mode
        "bar": dark.window,
        "bar-text": dark.text,
        "bar-muted": mix(dark.text, dark.window, 0.3),
        "bar-border": mix(dark.panel, dark.text, 0.22),
        "bar-hover": dark.panel,
        "bar-pressed": mix(dark.panel, dark.text, 0.09),
        "bar-accent": dark.accent,
        // App icon tiles, also dark in either mode
        "tile-top": mix(dark.panel, dark.text, 0.12),
        "tile-bottom": dark.panel,
    };
}

/** Every colour for one mode of a theme, with its pins applied. The taskbar
    and tiles are dark in either mode, so their pins come from [dark.pinned]. */
export function colours(theme, mode) {
    const out = workedOut(theme, mode);
    const darkOnly = key => key.startsWith("bar") || key.startsWith("tile");
    for (const [key, value] of Object.entries(theme.dark.pinned)) {
        if (darkOnly(key) || mode === "dark") {
            if (!(key in out)) {
                throw new Error(`${theme.title}: [dark.pinned] ${key} isn't a theme colour`);
            }
            out[key] = value;
        }
    }
    if (mode === "light") {
        for (const [key, value] of Object.entries(theme.light.pinned)) {
            if (!(key in out) || darkOnly(key)) {
                throw new Error(`${theme.title}: [light.pinned] ${key} isn't a light-mode theme colour`);
            }
            out[key] = value;
        }
    }
    return out;
}

/** The CSS custom properties for one mode of a theme. */
export function tokens(theme, mode) {
    const c = colours(theme, mode);
    const light = mode === "light";
    const css = Object.fromEntries(Object.entries(c).map(([k, v]) => [`--${k}`, v]));
    css["--icon-hover"] = light ? `color-mix(in srgb, ${c.surface} 60%, transparent)`
        : `color-mix(in srgb, ${c.bg} 45%, transparent)`;
    css["--wallpaper"] = `url("wallpaper-${theme[mode].wallpaper}"), url("wallpaper-${mode}.svg")`;
    return css;
}

/** Pairs that must read at WCAG AA (4.5:1), so a theme with any below it doesn't build. */
export function problems(name, theme) {
    const found = [];
    for (const mode of ["light", "dark"]) {
        const t = tokens(theme, mode);
        const pairs = [["text on panel", t["--text"], t["--surface"]], ["text on window", t["--text"], t["--bg"]],
                       ["muted on panel", t["--muted"], t["--surface"]], ["muted on window", t["--muted"], t["--bg"]],
                       ["accent text on accent", t["--accent-text"], t["--accent"]],
                       ["taskbar text", t["--bar-text"], t["--bar"]], ["taskbar muted", t["--bar-muted"], t["--bar"]]];
        for (const [what, fg, bg] of pairs) {
            const ratio = contrast(fg, bg);
            if (ratio < 4.5) {
                found.push(`${name} ${mode}: ${what} is ${ratio.toFixed(2)}:1`);
            }
        }
    }
    return found;
}

const block = (selector, values) =>
    `${selector} {\n${Object.entries(values).map(([key, value]) => `    ${key}: ${value};`).join("\n")}\n}\n`;

/** The stylesheet: each theme's light mode, then its dark mode chosen outright
    or by the system. Fellowship is the default, used when no theme is chosen. */
export function themesCss(themes = THEMES) {
    const failures = Object.entries(themes).flatMap(([name, theme]) => problems(name, theme));
    if (failures.length) {
        throw new Error("themes too hard to read:\n" + failures.join("\n"));
    }
    let css = "/* Built from themes/*.toml: change the colours there, not here */\n";
    for (const [name, theme] of Object.entries(themes)) {
        const at = name === "fellowship" ? ":root:not([data-palette])" : `:root[data-palette="${name}"]`;
        css += block(`${at}:not([data-theme="dark"])`, tokens(theme, "light"));
        css += `@media (prefers-color-scheme: dark) {\n${block(`${at}:not([data-theme="light"])`, tokens(theme, "dark"))
            .replace(/^/gm, "    ").trimEnd()}\n}\n`;
        css += block(`${at}[data-theme="dark"]`, tokens(theme, "dark"));
    }
    return css;
}

/** Every wallpaper file the themes use. */
export const wallpapers = (themes = THEMES) =>
    [...new Set(Object.values(themes).flatMap(t => [t.dark.wallpaper, t.light.wallpaper]))];

/** The theme list for the account menu: id, title, and the swatch it shows
    (the dark window and the dark accent). */
export function palettesTs(themes = THEMES) {
    const rows = Object.entries(themes).map(([id, t]) =>
        `    { id: ${JSON.stringify(id)}, title: ${JSON.stringify(t.title)}, swatch: [${JSON.stringify(t.dark.window)}, ${JSON.stringify(t.dark.accent)}] },`);
    return `// Built from themes/*.toml by build.mjs; don't edit.\nexport const PALETTES = [\n${rows.join("\n")}\n] as const;\n`;
}
