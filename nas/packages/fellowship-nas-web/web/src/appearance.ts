// The desktop's look: light, dark or the system's, and which theme. Both are
// remembered in this browser and put on the page before it first draws, so a
// reload never flashes the wrong one.
import { useState } from "preact/hooks";

export type Mode = "system" | "light" | "dark";

/** The themes, with the colours their choice shows: the dark window and the accent.
    The full palettes live in themes.mjs, which builds themes.css. */
export const PALETTES = [
    { id: "fellowship", title: "Fellowship", swatch: ["#101a17", "#c5a465"] },
    { id: "silverwater", title: "Silverwater", swatch: ["#192b3b", "#9ac8e7"] },
    { id: "hearthfield", title: "Hearthfield", swatch: ["#292d1f", "#e5aa75"] },
    { id: "highwatch", title: "Highwatch", swatch: ["#252126", "#d5a4be"] },
] as const;

export type Palette = typeof PALETTES[number]["id"];

function read<T extends string>(key: string, fallback: T, allowed: readonly string[]): T {
    try {
        const saved = localStorage.getItem(key);
        return saved && allowed.includes(saved) ? saved as T : fallback;
    } catch {
        return fallback;
    }
}

function remember(key: string, value: string) {
    try {
        localStorage.setItem(key, value);
    } catch {
        // Remembered for this visit only
    }
}

export function apply(mode: Mode, palette: Palette) {
    const root = document.documentElement;
    if (mode === "system") {
        root.removeAttribute("data-theme");
    } else {
        root.setAttribute("data-theme", mode);
    }
    // The default theme is the stylesheet's own colours
    if (palette === "fellowship") {
        root.removeAttribute("data-palette");
    } else {
        root.setAttribute("data-palette", palette);
    }
}

export const savedMode = () => read<Mode>("fellowship-theme", "system", ["system", "light", "dark"]);
export const savedPalette = () => read<Palette>("fellowship-palette", "fellowship", PALETTES.map(p => p.id));

/** The current mode and theme, and setters that show and remember a change at once. */
export function useAppearance() {
    const [mode, setMode] = useState<Mode>(savedMode);
    const [palette, setPalette] = useState<Palette>(savedPalette);
    return {
        mode, palette,
        setMode: (next: Mode) => { setMode(next); remember("fellowship-theme", next); apply(next, palette); },
        setPalette: (next: Palette) => { setPalette(next); remember("fellowship-palette", next); apply(mode, next); },
    };
}
