// FellowshipNAS's own icons: a coloured tile with a white line drawing
import { JSX } from "preact";

const GLYPHS = {
    setup: <path d="M13 17l3 3 5-6M26 17h9M13 30l3 3 5-6M26 31h9" />,
    files: <>
        <path d="M12 13h9l4 4v18a1.5 1.5 0 0 1-1.5 1.5h-16A1.5 1.5 0 0 1 6 35V15a2 2 0 0 1 2-2h4z" />
        <path d="M21 13v5h4" />
    </>,
    storage: <>
        <ellipse cx="24" cy="15" rx="10" ry="4" />
        <path d="M14 15v18c0 2.2 4.5 4 10 4s10-1.8 10-4V15M14 24c0 2.2 4.5 4 10 4s10-1.8 10-4" />
    </>,
    shares: <>
        <path d="M11 17a2 2 0 0 1 2-2h7l3 3h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H13a2 2 0 0 1-2-2z" />
        <path d="M19 27h10M26 24l3 3-3 3" />
    </>,
    users: <>
        <circle cx="20" cy="18" r="5" />
        <path d="M11 35c0-5 4-9 9-9s9 4 9 9M29 14a5 5 0 0 1 0 9M33 27c2.5 1.5 4 4.5 4 8" />
    </>,
    backups: <>
        <path d="M14 24a10 10 0 1 0 3-7.1" />
        <path d="M13 12v6h6M24 18v6l4 3" />
    </>,
    health: <path d="M10 25h7l3-7 4 13 3-6h11" />,
    alerts: <path d="M16 30v-8a8 8 0 0 1 16 0v8l2.5 3h-21zM21.5 37h5" />,
    settings: <>
        <path d="M12 16h24M12 24h24M12 32h24" />
        <circle cx="18" cy="16" r="3" style={{ fill: "var(--bar-accent)", stroke: "var(--bar-accent)" }} />
        <circle cx="30" cy="24" r="3" style={{ fill: "var(--bar-accent)", stroke: "var(--bar-accent)" }} />
        <circle cx="21" cy="32" r="3" style={{ fill: "var(--bar-accent)", stroke: "var(--bar-accent)" }} />
    </>,
};

export type AppIconName = keyof typeof GLYPHS;

/** A tile in the theme's dark colours with a fine accent edge and a light drawing, the same in light and dark:
    moss, gold and parchment in the default theme. */
export function AppIcon({ name, size = 48 }: { name: AppIconName; size?: number }) {
    return (
        <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden="true" class="app-icon">
            <defs>
                <linearGradient id="fellowship-tile" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0" style={{ stopColor: "var(--tile-top)" }} />
                    <stop offset="1" style={{ stopColor: "var(--tile-bottom)" }} />
                </linearGradient>
            </defs>
            <rect x="2" y="2" width="44" height="44" rx="11" fill="url(#fellowship-tile)" />
            <rect x="2.6" y="2.6" width="42.8" height="42.8" rx="10.4" fill="none" style={{ stroke: "var(--bar-accent)" }}
                  stroke-opacity="0.6" stroke-width="1.2" />
            <g fill="none" style={{ stroke: "var(--bar-text)" }} stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">
                {GLYPHS[name]}
            </g>
        </svg>
    );
}

const UI = {
    minimize: <path d="M6 12h12" />,
    maximize: <rect x="6" y="6" width="12" height="12" rx="1.5" />,
    restore: <path d="M9 9V6.5A.5.5 0 0 1 9.5 6h8a.5.5 0 0 1 .5.5v8a.5.5 0 0 1-.5.5H15M6 9.5a.5.5 0 0 1 .5-.5h8a.5.5 0 0 1 .5.5v8a.5.5 0 0 1-.5.5h-8a.5.5 0 0 1-.5-.5z" />,
    close: <path d="M7 7l10 10M17 7L7 17" />,
    launcher: <path d="M5 5h4v4H5zM10 5h4v4h-4zM15 5h4v4h-4zM5 10h4v4H5zM10 10h4v4h-4zM15 10h4v4h-4zM5 15h4v4H5zM10 15h4v4h-4zM15 15h4v4h-4z" />,
    bell: <path d="M8 16v-5a4 4 0 0 1 8 0v5l1.5 2h-11zM10.5 21h3" />,
    user: <path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM5 20c1-3.5 3.8-5.5 7-5.5s6 2 7 5.5" />,
};

export function Glyph({ name }: { name: keyof typeof UI }): JSX.Element {
    return (
        <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"
             stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            {UI[name]}
        </svg>
    );
}

/** The FellowshipOS mark: three peaks under a gold star */
export function Mark({ size = 44 }: { size?: number }) {
    return (
        <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden="true">
            <rect x="2" y="2" width="44" height="44" rx="11" fill="#1b2923" />
            <rect x="2.6" y="2.6" width="42.8" height="42.8" rx="10.4" fill="none" stroke="#c5a465" stroke-width="1.2" />
            <path d="M8 35l9-13 5 7 6-11 12 17z" fill="#29392f" stroke="#efe7d5" stroke-width="1.8" stroke-linejoin="round" />
            <path d="M26 21.5l2-3.5 2.4 3.4" fill="none" stroke="#efe7d5" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
            <path d="M34 10l1.1 2.4 2.4 1.1-2.4 1.1L34 17l-1.1-2.4-2.4-1.1 2.4-1.1z" fill="#c5a465" />
        </svg>
    );
}
