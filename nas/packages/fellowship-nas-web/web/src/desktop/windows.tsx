// Windows on the desktop: open, move, resize, stack, minimise and maximise,
// with the layout remembered per user in this browser
import { ComponentChildren } from "preact";
import { useEffect, useState } from "preact/hooks";
import { APPS, AppId } from "./apps";
import { AppIcon, Glyph } from "./icons";

export interface Win {
    id: number;
    app: AppId;
    x: number;
    y: number;
    w: number;
    h: number;
    z: number;
    minimized: boolean;
    maximized: boolean;
}

type Geometry = Pick<Win, "x" | "y" | "w" | "h">;

interface Saved {
    windows: Win[];
    // Where each app was last, so it reopens there
    places: Partial<Record<AppId, Geometry & { maximized: boolean }>>;
}

export const TASKBAR = 48;
const MIN_W = 360;
const MIN_H = 240;

function area() {
    return { w: window.innerWidth, h: window.innerHeight - TASKBAR };
}

function clamp(geometry: Geometry): Geometry {
    const { w: width, h: height } = area();
    const w = Math.max(MIN_W, Math.min(geometry.w, width));
    const h = Math.max(MIN_H, Math.min(geometry.h, height));
    // Keep enough of the title bar on screen to grab it again
    const x = Math.min(Math.max(geometry.x, 120 - w), width - 120);
    const y = Math.min(Math.max(geometry.y, 0), height - 40);
    return { x, y, w, h };
}

function load(key: string, allowed: AppId[]): Saved {
    try {
        const saved = JSON.parse(localStorage.getItem(key) || "null") as Saved | null;
        if (saved && Array.isArray(saved.windows)) {
            return { places: saved.places || {},
                     windows: saved.windows.filter(win => allowed.includes(win.app)).map(win => ({ ...win, ...clamp(win) })) };
        }
    } catch {
        // Unreadable or blocked storage: start with a clear desktop
    }
    return { windows: [], places: {} };
}

export function useWindows(user: string, allowed: AppId[]) {
    const key = `fellowship-desktop/${user}`;
    const [state, setState] = useState<Saved>(() => load(key, allowed));

    useEffect(() => {
        try {
            localStorage.setItem(key, JSON.stringify(state));
        } catch {
            // The layout just won't be remembered
        }
    }, [state]);

    const top = (windows: Win[]) => windows.reduce((highest, win) => Math.max(highest, win.z), 0) + 1;
    const update = (id: number, change: (win: Win) => Partial<Win>) => setState(previous => {
        const windows = previous.windows.map(win => win.id === id ? { ...win, ...change(win) } : win);
        const changed = windows.find(win => win.id === id);
        const places = changed
            ? { ...previous.places, [changed.app]: { x: changed.x, y: changed.y, w: changed.w, h: changed.h, maximized: changed.maximized } }
            : previous.places;
        return { windows, places };
    });

    return {
        windows: state.windows,
        open(app: AppId) {
            setState(previous => {
                const existing = previous.windows.find(win => win.app === app);
                if (existing) {
                    return { ...previous, windows: previous.windows.map(win =>
                        win.id === existing.id ? { ...win, minimized: false, z: top(previous.windows) } : win) };
                }
                const { w: width, h: height } = area();
                const place = previous.places[app];
                const offset = (previous.windows.length % 6) * 28;
                const w = Math.min(APPS[app].width, width - 40);
                const h = Math.min(APPS[app].height, height - 40);
                const geometry = place ? clamp(place) : clamp({ w, h, x: Math.round((width - w) / 2) + offset - 70,
                                                              y: Math.max(16, Math.round((height - h) / 3)) + offset });
                const id = previous.windows.reduce((highest, win) => Math.max(highest, win.id), 0) + 1;
                return { ...previous, windows: [...previous.windows, { id, app, ...geometry, z: top(previous.windows),
                                                                      minimized: false, maximized: place?.maximized || false }] };
            });
        },
        close(id: number) {
            setState(previous => ({ ...previous, windows: previous.windows.filter(win => win.id !== id) }));
        },
        focus(id: number) {
            setState(previous => {
                const target = previous.windows.find(win => win.id === id);
                if (!target || target.z === top(previous.windows) - 1) {
                    return previous;
                }
                return { ...previous, windows: previous.windows.map(win => win.id === id ? { ...win, z: top(previous.windows) } : win) };
            });
        },
        minimize: (id: number) => update(id, () => ({ minimized: true })),
        restore(id: number) {
            setState(previous => ({ ...previous, windows: previous.windows.map(win =>
                win.id === id ? { ...win, minimized: false, z: top(previous.windows) } : win) }));
        },
        toggleMaximized: (id: number) => update(id, win => ({ maximized: !win.maximized })),
        place: (id: number, geometry: Geometry) => update(id, () => clamp(geometry)),
    };
}

export type Windows = ReturnType<typeof useWindows>;

type Edge = "n" | "e" | "s" | "w" | "ne" | "se" | "sw" | "nw";
const EDGES: Edge[] = ["n", "e", "s", "w", "ne", "se", "sw", "nw"];

/** Follow the pointer from a press until it is released, reporting how far it moved. */
function drag(event: PointerEvent, onMove: (dx: number, dy: number) => void) {
    const target = event.currentTarget as HTMLElement;
    const startX = event.clientX;
    const startY = event.clientY;
    target.setPointerCapture(event.pointerId);
    const move = (moved: PointerEvent) => onMove(moved.clientX - startX, moved.clientY - startY);
    const stop = () => {
        target.removeEventListener("pointermove", move);
        target.removeEventListener("pointerup", stop);
        target.removeEventListener("pointercancel", stop);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", stop);
    target.addEventListener("pointercancel", stop);
}

export function Window({ win, active, windows, children }: {
    win: Win;
    active: boolean;
    windows: Windows;
    children: ComponentChildren;
}) {
    const app = APPS[win.app];

    // Bring the window into view when the browser shrinks
    useEffect(() => {
        const fit = () => windows.place(win.id, win);
        window.addEventListener("resize", fit);
        return () => window.removeEventListener("resize", fit);
    }, [win.x, win.y, win.w, win.h]);

    const startMove = (event: PointerEvent) => {
        if (win.maximized || (event.target as HTMLElement).closest("button") || event.button !== 0) {
            return;
        }
        event.preventDefault();
        const start = { ...win };
        drag(event, (dx, dy) => windows.place(win.id, { ...start, x: start.x + dx, y: start.y + dy }));
    };

    const startResize = (edge: Edge) => (event: PointerEvent) => {
        if (event.button !== 0) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        windows.focus(win.id);
        const start = { ...win };
        drag(event, (dx, dy) => {
            const next = { x: start.x, y: start.y, w: start.w, h: start.h };
            if (edge.includes("e")) {
                next.w = start.w + dx;
            }
            if (edge.includes("s")) {
                next.h = start.h + dy;
            }
            if (edge.includes("w")) {
                next.w = Math.max(MIN_W, start.w - dx);
                next.x = start.x + start.w - next.w;
            }
            if (edge.includes("n")) {
                next.h = Math.max(MIN_H, start.h - dy);
                next.y = start.y + start.h - next.h;
            }
            windows.place(win.id, next);
        });
    };

    const style = win.maximized ? { zIndex: win.z }
        : { zIndex: win.z, left: `${win.x}px`, top: `${win.y}px`, width: `${win.w}px`, height: `${win.h}px` };

    return (
        <section class={`window${active ? " active" : ""}${win.maximized ? " maximized" : ""}`}
                 style={style} hidden={win.minimized} role="dialog" aria-label={app.title}
                 onPointerDown={() => windows.focus(win.id)}>
            <header class="window-title" onPointerDown={startMove} onDblClick={event => {
                if (!(event.target as HTMLElement).closest("button")) {
                    windows.toggleMaximized(win.id);
                }
            }}>
                <AppIcon name={win.app} size={20} />
                <span class="window-name">{app.title}</span>
                <div class="window-buttons">
                    <button type="button" title="Minimise" aria-label="Minimise" onClick={() => windows.minimize(win.id)}>
                        <Glyph name="minimize" />
                    </button>
                    <button type="button" class="hide-narrow" title={win.maximized ? "Restore" : "Maximise"}
                            aria-label={win.maximized ? "Restore" : "Maximise"} onClick={() => windows.toggleMaximized(win.id)}>
                        <Glyph name={win.maximized ? "restore" : "maximize"} />
                    </button>
                    <button type="button" class="close" title="Close" aria-label="Close" onClick={() => windows.close(win.id)}>
                        <Glyph name="close" />
                    </button>
                </div>
            </header>
            <div class="window-body">{children}</div>
            {!win.maximized && EDGES.map(edge => (
                <div key={edge} class={`resize resize-${edge}`} onPointerDown={startResize(edge)} />
            ))}
        </section>
    );
}
