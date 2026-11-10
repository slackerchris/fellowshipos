// Chris's themes beyond the default forest and gold. Each gives four colours
// for each mode (window, panel, text and accent) and a wallpaper; the rest is
// worked out from those, so a theme is changed by changing its four colours.
// The build stops if a theme's text would be hard to read.

export const THEMES = {
    silverwater: {
        title: "Silverwater",
        wallpaper: "silverwater.webp",
        light: { window: "#e6edf3", panel: "#f6f9fc", text: "#22374b", accent: "#366b91" },
        dark: { window: "#192b3b", panel: "#243c50", text: "#e9f3fa", accent: "#9ac8e7" },
    },
    hearthfield: {
        title: "Hearthfield",
        wallpaper: "hearthfield.webp",
        light: { window: "#eee5cf", panel: "#fbf6e8", text: "#362a1d", accent: "#8e492c" },
        dark: { window: "#292d1f", panel: "#3a3f2a", text: "#f5ead5", accent: "#e5aa75" },
    },
    highwatch: {
        title: "Highwatch",
        wallpaper: "highwatch.webp",
        light: { window: "#e9e1df", panel: "#f7f1eb", text: "#352b35", accent: "#754963" },
        dark: { window: "#252126", panel: "#37303a", text: "#f4ece4", accent: "#d5a4be" },
    },
};

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

/** Every colour the desktop uses, for one mode of a theme. */
export function tokens(theme, mode) {
    const c = theme[mode];
    const dark = theme.dark;
    const light = mode === "light";
    return {
        "--desktop": c.window,
        "--bg": c.window,
        "--surface": c.panel,
        "--surface-sunk": light ? mix(c.window, c.text, 0.04) : mix(c.window, c.panel, 0.5),
        "--raised": light ? mix(c.window, c.text, 0.09) : mix(c.panel, c.text, 0.09),
        "--border": light ? mix(c.window, c.text, 0.22) : mix(c.panel, c.text, 0.22),
        "--text": c.text,
        "--muted": mix(c.text, c.panel, light ? 0.22 : 0.3),
        "--accent": c.accent,
        "--accent-text": light ? c.panel : c.window,
        "--gold": c.accent,
        "--icon-label": c.text,
        "--icon-hover": light ? `color-mix(in srgb, ${c.panel} 60%, transparent)`
            : `color-mix(in srgb, ${c.window} 45%, transparent)`,
        "--wallpaper": `url("wallpaper-${theme.wallpaper}"), url("wallpaper-${mode}.svg")`,
        // The taskbar is dark in either mode
        "--bar": dark.window,
        "--bar-text": dark.text,
        "--bar-muted": mix(dark.text, dark.window, 0.3),
        "--bar-border": mix(dark.panel, dark.text, 0.22),
        "--bar-hover": dark.panel,
        "--bar-pressed": mix(dark.panel, dark.text, 0.09),
        "--bar-accent": dark.accent,
        // App icon tiles, also dark in either mode
        "--tile-top": mix(dark.panel, dark.text, 0.12),
        "--tile-bottom": dark.panel,
    };
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

/** The stylesheet: each theme's light mode, then its dark mode chosen outright or by the system. */
export function themesCss() {
    const failures = Object.entries(THEMES).flatMap(([name, theme]) => problems(name, theme));
    if (failures.length) {
        throw new Error("themes too hard to read:\n" + failures.join("\n"));
    }
    let css = "/* Built from themes.mjs: change the four colours there, not here */\n";
    for (const [name, theme] of Object.entries(THEMES)) {
        const at = `:root[data-palette="${name}"]`;
        css += block(`${at}:not([data-theme="dark"])`, tokens(theme, "light"));
        css += `@media (prefers-color-scheme: dark) {\n${block(`${at}:not([data-theme="light"])`, tokens(theme, "dark"))
            .replace(/^/gm, "    ").trimEnd()}\n}\n`;
        css += block(`${at}[data-theme="dark"]`, tokens(theme, "dark"));
    }
    return css;
}
