// The desktop's look: light, dark or the system's, and which theme. The choice
// is the person's, kept on the NAS so it follows them to another computer, and
// copied into this browser so the page paints the right one before the server
// answers.
import { useState } from "preact/hooks";
import { fnasJson } from "./api";
import { PALETTES } from "./themes.gen";

export type Mode = "system" | "light" | "dark";

export const MODES: readonly Mode[] = ["system", "light", "dark"];

/** The themes, with the colours their choice shows: the dark window and the
    accent. Built from the shared theme files (themes/*.toml) by build.mjs. */
export { PALETTES };

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

const MODE_KEY = "fellowship-theme";
const PALETTE_KEY = "fellowship-palette";

export const savedMode = () => read<Mode>(MODE_KEY, "system", MODES);
export const savedPalette = () => read<Palette>(PALETTE_KEY, "fellowship", PALETTES.map(p => p.id));

// Whose look this browser is showing, so a change is saved for the right person
let owner = "";

function known<T extends string>(value: unknown, allowed: readonly string[], fallback: T): T {
    return typeof value === "string" && allowed.includes(value) ? value as T : fallback;
}

/** Show the look the NAS remembers for this person. The browser's copy has
    already painted something, so this only puts it right. */
export async function loadLook(user: string) {
    owner = user;
    const saved = await fnasJson<{ mode?: string; palette?: string }>(["look", "show", user]);
    const mode = known(saved.mode, MODES, "system");
    const palette = known(saved.palette, PALETTES.map(p => p.id), "fellowship");
    remember(MODE_KEY, mode);
    remember(PALETTE_KEY, palette);
    apply(mode, palette);
}

/** Save a change for whoever is signed in. It is already on the page, so a NAS
    that can't keep it only means this browser remembers it alone. */
function saveLook(change: { mode?: Mode; palette?: Palette }) {
    if (!owner) {
        return;
    }
    const args = ["look", "set", owner];
    if (change.mode) {
        args.push("--mode", change.mode);
    }
    if (change.palette) {
        args.push("--palette", change.palette);
    }
    fnasJson(args).catch(() => {});
}

/** The current mode and theme, and setters that show and remember a change at once. */
export function useAppearance() {
    const [mode, setMode] = useState<Mode>(savedMode);
    const [palette, setPalette] = useState<Palette>(savedPalette);
    return {
        mode, palette,
        setMode: (next: Mode) => {
            setMode(next); remember(MODE_KEY, next); apply(next, palette); saveLook({ mode: next });
        },
        setPalette: (next: Palette) => {
            setPalette(next); remember(PALETTE_KEY, next); apply(mode, next); saveLook({ palette: next });
        },
    };
}
