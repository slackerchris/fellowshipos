// The desktop's look: light, dark or the system's, which theme, and how the clock reads. The choice
// is the person's, kept on the NAS so it follows them to another computer, and
// copied into this browser so the page paints the right one before the server
// answers.
import { useEffect, useState } from "preact/hooks";
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

/** How the clock reads: 12- or 24-hour or the device's way, seconds, and a
    date beside the time on the taskbar. */
export interface ClockFormat {
    hours: "auto" | "12" | "24";
    seconds: boolean;
    date: "off" | "short" | "numeric";
}

export const HOURS: readonly ClockFormat["hours"][] = ["auto", "12", "24"];
export const DATES: readonly ClockFormat["date"][] = ["off", "short", "numeric"];
const CLOCK_KEY = "fellowship-clock";
const CLOCK_DEFAULT: ClockFormat = { hours: "auto", seconds: false, date: "off" };

function cleanClock(value: Partial<Record<keyof ClockFormat, unknown>>): ClockFormat {
    return {
        hours: known(value.hours, HOURS, CLOCK_DEFAULT.hours),
        seconds: typeof value.seconds === "boolean" ? value.seconds : CLOCK_DEFAULT.seconds,
        date: known(value.date, DATES, CLOCK_DEFAULT.date),
    };
}

let clock: ClockFormat = (() => {
    try {
        return cleanClock(JSON.parse(localStorage.getItem(CLOCK_KEY) || "{}"));
    } catch {
        return CLOCK_DEFAULT;
    }
})();
const clockListeners = new Set<(format: ClockFormat) => void>();

function showClock(next: ClockFormat) {
    clock = next;
    remember(CLOCK_KEY, JSON.stringify(next));
    clockListeners.forEach(listener => listener(next));
}

/** The clock format, kept current on every clock on the page when it changes. */
export function useClockFormat() {
    const [format, setFormat] = useState(clock);
    useEffect(() => {
        clockListeners.add(setFormat);
        setFormat(clock);
        return () => { clockListeners.delete(setFormat); };
    }, []);
    return {
        format,
        setClock: (change: Partial<ClockFormat>) => {
            showClock({ ...clock, ...change });
            saveLook(change);
        },
    };
}

const hourOptions = (format: ClockFormat): Intl.DateTimeFormatOptions =>
    format.hours === "12" ? { hour12: true } : format.hours === "24" ? { hourCycle: "h23" } : {};

/** The time as this person likes it; seconds when they asked for them, or when forced. */
export function formatTime(date: Date, format: ClockFormat, seconds = format.seconds, timeZone?: string) {
    // A 12-hour clock reads 3:57 PM; a 24-hour one keeps its leading zero, 09:05
    const cycle = new Intl.DateTimeFormat(undefined, { hour: "numeric", ...hourOptions(format) }).resolvedOptions().hourCycle;
    return date.toLocaleTimeString(undefined, { hour: cycle === "h23" || cycle === "h24" ? "2-digit" : "numeric",
        minute: "2-digit", ...(seconds ? { second: "2-digit" } : {}), ...hourOptions(format), timeZone });
}

/** The date the taskbar shows beside the time, or nothing. */
export function formatTaskbarDate(date: Date, format: ClockFormat, timeZone?: string) {
    if (format.date === "short") {
        return date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", timeZone });
    }
    return format.date === "numeric" ? date.toLocaleDateString(undefined, { dateStyle: "short", timeZone }) : "";
}

/** Show the look the NAS remembers for this person. The browser's copy has
    already painted something, so this only puts it right. */
export async function loadLook(user: string) {
    owner = user;
    const saved = await fnasJson<{ mode?: string; palette?: string; hours?: unknown; seconds?: unknown; date?: unknown }>(
        ["look", "show", user]);
    const mode = known(saved.mode, MODES, "system");
    const palette = known(saved.palette, PALETTES.map(p => p.id), "fellowship");
    remember(MODE_KEY, mode);
    remember(PALETTE_KEY, palette);
    apply(mode, palette);
    showClock(cleanClock(saved));
}

/** Save a change for whoever is signed in. It is already on the page, so a NAS
    that can't keep it only means this browser remembers it alone. */
function saveLook(change: { mode?: Mode; palette?: Palette } & Partial<ClockFormat>) {
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
    if (change.hours) {
        args.push("--hours", change.hours);
    }
    if (change.seconds !== undefined) {
        args.push("--seconds", change.seconds ? "on" : "off");
    }
    if (change.date) {
        args.push("--date", change.date);
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
