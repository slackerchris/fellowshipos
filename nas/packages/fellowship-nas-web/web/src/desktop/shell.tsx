// The desktop: wallpaper, app icons, widgets, windows and the taskbar
import { useContext, useEffect, useState } from "preact/hooks";
import { Alert, Health, Pool, fnasJson, humanSize, logout, when } from "../api";
import { SEVERITY } from "../health";
import { SessionContext } from "../settings";
import { Badge, Usage } from "../ui";
import { APPS, AppId, appsFor } from "./apps";
import { AppIcon, Glyph } from "./icons";
import { Win, Window, useWindows } from "./windows";

const POLL = 120_000;

interface Status {
    pools?: Pool[];
    health?: Health;
    alerts?: Alert[];
    error?: string;
}

/** What the widgets and notification centre show, refreshed in the background. */
function useStatus(admin: boolean) {
    const [status, setStatus] = useState<Status>({});
    useEffect(() => {
        if (!admin) {
            return;
        }
        let current = true;
        const refresh = async () => {
            try {
                const [pools, health, alerts] = await Promise.all([
                    fnasJson<Pool[]>(["pool", "list"], true),
                    fnasJson<Health>(["health"], true),
                    fnasJson<Alert[]>(["alert", "list", "--limit", "5"], true),
                ]);
                current && setStatus({ pools, health, alerts });
            } catch (error) {
                current && setStatus(previous => ({ ...previous, error: (error as Error).message }));
            }
        };
        refresh();
        const timer = setInterval(refresh, POLL);
        return () => { current = false; clearInterval(timer); };
    }, [admin]);
    return status;
}

interface Problem {
    key: string;
    severity: "info" | "warning" | "critical";
    title: string;
    message: string;
}

function problems(status: Status): Problem[] {
    const pools = (status.pools || []).filter(pool => pool.health !== "ONLINE").map(pool => ({
        key: `pool-${pool.name}`, severity: "critical" as const, title: `Pool ${pool.name} is ${pool.health.toLowerCase()}`,
        message: "A disk is missing or failing. Storage shows which.",
    }));
    return [...pools, ...(status.health?.problems || [])];
}

function Clock() {
    const [now, setNow] = useState(new Date());
    useEffect(() => {
        const timer = setInterval(() => setNow(new Date()), 15_000);
        return () => clearInterval(timer);
    }, []);
    return (
        <time class="clock" dateTime={now.toISOString()} title={now.toLocaleDateString(undefined, { dateStyle: "full" })}>
            {now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
        </time>
    );
}

function Widgets({ status, open }: { status: Status; open: (app: AppId) => void }) {
    const disks = (status.health?.disks || []).filter(disk => disk.state !== "os");
    const hottest = disks.reduce<number | null>((max, disk) =>
        disk.temperature !== null && (max === null || disk.temperature > max) ? disk.temperature : max, null);
    const failing = disks.filter(disk => disk.verdict === "failing").length;
    const watch = disks.filter(disk => disk.verdict === "watch").length;
    return (
        <aside class="widgets" aria-label="Status">
            <button type="button" class="widget" onClick={() => open("storage")}>
                <h2>Storage</h2>
                {!status.pools && <p class="muted small">{status.error || "Loading…"}</p>}
                {status.pools && !status.pools.length && <p class="small">No pools yet. Open Setup to create one.</p>}
                {status.pools?.map(pool => (
                    <div key={pool.name} class="widget-row">
                        <div class="row">
                            <strong>{pool.name}</strong>
                            <Badge kind={pool.health === "ONLINE" ? "good" : pool.health === "DEGRADED" ? "warning" : "bad"}>
                                {pool.health === "ONLINE" ? "healthy" : pool.health.toLowerCase()}</Badge>
                        </div>
                        <Usage used={pool.allocated} total={pool.size} />
                        <span class="small muted">{humanSize(pool.free)} free of {humanSize(pool.size)}</span>
                    </div>
                ))}
            </button>
            <button type="button" class="widget" onClick={() => open("health")}>
                <h2>Disks</h2>
                {!status.health && <p class="muted small">{status.error ? "Unavailable" : "Loading…"}</p>}
                {status.health && (
                    <div class="stack">
                        <div class="row">
                            <span>{disks.length} data disk{disks.length === 1 ? "" : "s"}</span>
                            {failing > 0 && <Badge kind="bad">{failing} failing</Badge>}
                            {watch > 0 && <Badge kind="warning">{watch} to watch</Badge>}
                            {!failing && !watch && disks.length > 0 && <Badge kind="good">all healthy</Badge>}
                        </div>
                        {hottest !== null && <span class="small muted">Warmest {hottest} °C</span>}
                    </div>
                )}
            </button>
            <button type="button" class="widget" onClick={() => open("alerts")}>
                <h2>Recent alerts</h2>
                {status.alerts && !status.alerts.length && <p class="small muted">None yet.</p>}
                {[...(status.alerts || [])].reverse().slice(0, 3).map(alert => (
                    <div key={`${alert.time}-${alert.title}`} class="widget-row">
                        <div class="row"><Badge kind={SEVERITY[alert.severity]}>{alert.severity}</Badge>
                            <span class="small">{alert.title}</span></div>
                        <span class="small muted">{when(alert.time)}</span>
                    </div>
                ))}
            </button>
        </aside>
    );
}

type Panel = "launcher" | "notifications" | "user" | null;

function Notifications({ status, open }: { status: Status; open: (app: AppId) => void }) {
    const found = problems(status);
    return (
        <div class="panel panel-right" role="dialog" aria-label="Notifications">
            <h2>Notifications</h2>
            {!found.length && <p class="muted small">Nothing needs attention.</p>}
            {found.map(problem => (
                <button type="button" key={problem.key} class="notification" onClick={() => open("health")}>
                    <Badge kind={SEVERITY[problem.severity]}>{problem.severity}</Badge> <strong>{problem.title}</strong>
                    <p class="small muted">{problem.message}</p>
                </button>
            ))}
            {(status.alerts || []).length > 0 && <h3>Sent recently</h3>}
            {[...(status.alerts || [])].reverse().map(alert => (
                <button type="button" key={`${alert.time}-${alert.title}`} class="notification" onClick={() => open("alerts")}>
                    <strong>{alert.title}</strong> <span class="small muted">{when(alert.time)}</span>
                    <p class="small muted">{alert.message}</p>
                </button>
            ))}
        </div>
    );
}

type Theme = "system" | "light" | "dark";

function useTheme(): [Theme, (theme: Theme) => void] {
    const read = (): Theme => {
        try {
            return (localStorage.getItem("fellowship-theme") as Theme) || "system";
        } catch {
            return "system";
        }
    };
    const [theme, setTheme] = useState<Theme>(read);
    useEffect(() => {
        if (theme === "system") {
            document.documentElement.removeAttribute("data-theme");
        } else {
            document.documentElement.setAttribute("data-theme", theme);
        }
        try {
            localStorage.setItem("fellowship-theme", theme);
        } catch {
            // Remembered for this visit only
        }
    }, [theme]);
    return [theme, setTheme];
}

function UserMenu({ open, onSignOut }: { open: (app: AppId) => void; onSignOut: () => void }) {
    const { user, admin } = useContext(SessionContext);
    const [theme, setTheme] = useTheme();
    return (
        <div class="panel panel-right panel-small" role="menu" aria-label="Account">
            <p><strong>{user}</strong><br /><span class="small muted">{admin ? "Administrator" : "User"}</span></p>
            <div class="segmented" role="group" aria-label="Theme">
                {(["system", "light", "dark"] as Theme[]).map(option => (
                    <button key={option} type="button" class={theme === option ? "current" : ""} aria-pressed={theme === option}
                            onClick={() => setTheme(option)}>{option === "system" ? "Auto" : option[0].toUpperCase() + option.slice(1)}</button>
                ))}
            </div>
            <button type="button" class="menu-item" role="menuitem" onClick={() => open("settings")}>Settings</button>
            <button type="button" class="menu-item" role="menuitem" onClick={onSignOut}>Sign out</button>
        </div>
    );
}

function Launcher({ apps, open }: { apps: AppId[]; open: (app: AppId) => void }) {
    return (
        <div class="panel panel-left launcher" role="dialog" aria-label="Apps">
            {apps.map(app => (
                <button type="button" key={app} class="launcher-app" onClick={() => open(app)}>
                    <AppIcon name={app} size={40} />
                    <span><strong>{APPS[app].title}</strong><br /><span class="small muted">{APPS[app].blurb}</span></span>
                </button>
            ))}
        </div>
    );
}

export function Desktop({ onSignedOut }: { onSignedOut: () => void }) {
    const session = useContext(SessionContext);
    const apps = appsFor(session.admin);
    const windows = useWindows(session.user, apps);
    const status = useStatus(session.admin);
    const [panel, setPanel] = useState<Panel>(null);
    const found = problems(status);

    useEffect(() => {
        const escape = (event: KeyboardEvent) => event.key === "Escape" && setPanel(null);
        window.addEventListener("keydown", escape);
        return () => window.removeEventListener("keydown", escape);
    }, []);

    const open = (app: AppId) => {
        setPanel(null);
        windows.open(app);
    };
    const toggle = (which: Panel) => setPanel(panel === which ? null : which);
    const visible = windows.windows.filter(win => !win.minimized);
    const front = visible.reduce<Win | null>((best, win) => !best || win.z > best.z ? win : best, null)?.id;

    const signOut = async () => {
        try {
            await logout();
        } finally {
            onSignedOut();
        }
    };

    return (
        <div class="desktop">
            <main class="desktop-area" onPointerDown={event => event.target === event.currentTarget && setPanel(null)}>
                <nav class="desktop-icons" aria-label="Apps">
                    {apps.map(app => (
                        <button type="button" key={app} class="desktop-icon" onClick={() => open(app)}>
                            <AppIcon name={app} size={52} />
                            <span>{APPS[app].title}</span>
                        </button>
                    ))}
                </nav>
                {session.admin && <Widgets status={status} open={open} />}
                {windows.windows.map(win => {
                    const Body = APPS[win.app].body;
                    return (
                        <Window key={win.id} win={win} active={win.id === front} windows={windows}>
                            <Body />
                        </Window>
                    );
                })}
            </main>
            {panel === "launcher" && <Launcher apps={apps} open={open} />}
            {panel === "notifications" && <Notifications status={status} open={open} />}
            {panel === "user" && <UserMenu open={open} onSignOut={signOut} />}
            <footer class="taskbar">
                <button type="button" class={`taskbar-button ${panel === "launcher" ? "pressed" : ""}`} title="Apps"
                        aria-label="Apps" aria-expanded={panel === "launcher"} onClick={() => toggle("launcher")}>
                    <Glyph name="launcher" />
                </button>
                <div class="taskbar-windows">
                    {windows.windows.map(win => (
                        <button type="button" key={win.id}
                                class={`taskbar-window ${win.id === front ? "current" : ""} ${win.minimized ? "minimized" : ""}`}
                                onClick={() => win.minimized ? windows.restore(win.id)
                                    : win.id === front ? windows.minimize(win.id) : windows.focus(win.id)}>
                            <AppIcon name={win.app} size={22} />
                            <span>{APPS[win.app].title}</span>
                        </button>
                    ))}
                </div>
                {session.admin && (
                    <button type="button" class={`taskbar-button ${panel === "notifications" ? "pressed" : ""}`}
                            title="Notifications" aria-label={`Notifications: ${found.length}`}
                            aria-expanded={panel === "notifications"} onClick={() => toggle("notifications")}>
                        <Glyph name="bell" />
                        {found.length > 0 && <span class={`count ${found.some(p => p.severity === "critical") ? "bad" : ""}`}>
                            {found.length}</span>}
                    </button>
                )}
                <Clock />
                <button type="button" class={`taskbar-button user ${panel === "user" ? "pressed" : ""}`} title={session.user}
                        aria-label="Account" aria-expanded={panel === "user"} onClick={() => toggle("user")}>
                    <Glyph name="user" /><span class="hide-narrow">{session.user}</span>
                </button>
            </footer>
        </div>
    );
}
