// The desktop: wallpaper, app icons, widgets, windows and the taskbar
import { useContext, useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { Alert, DiskHealth, Health, Pool, SetupStatus, fnasJson, humanSize, logout, plural, when } from "../api";
import { SEVERITY, SEVERITY_WORD, Severity, level, severityOf } from "../health";
import { MODES, useAppearance } from "../appearance";
import { useJobs } from "../jobs";
import { SessionContext } from "../settings";
import { Badge, Usage } from "../ui";
import { ArcGauge, Gauge, MetricIcon } from "../charts";
import { APPS, AppId, appsFor, iconsFor } from "./apps";
import { DesktopContext } from "./context";
import { AppIcon, Glyph } from "./icons";
import { focusDisk } from "./focus";
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
    severity: Alert["severity"];
    title: string;
    message: string;
}

// Health's own checks cover pools that lost a disk, so nothing is listed twice
function problems(status: Status): Problem[] {
    return status.health?.problems || [];
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

/** The checks at a glance: the worst problem, CPU and RAM, and each pool's fill.
    The whole card opens Health. */
function HealthWidget({ status, open }: { status: Status; open: (app: AppId) => void }) {
    const health = status.health;
    const checks = health?.checks || [];
    const passed = checks.filter(check => check.state === "passed").length;
    const [worst] = health?.problems || [];
    const severity = severityOf(worst);
    const system = health?.system;
    const memoryUsed = system ? system.memory.total - system.memory.available : 0;
    const memoryPercent = system?.memory.total ? Math.round((memoryUsed * 100) / system.memory.total) : 0;
    const pools = status.pools || [];
    return (
        <button type="button" class="widget health-widget" onClick={() => open("health")}>
            <span class="widget-head"><h2>Health</h2><span class="chevron" aria-hidden="true">›</span></span>
            {!health && <p class="muted small">{status.error ? "Unavailable" : "Loading…"}</p>}
            {health && system && <>
                <span class="medallion-row">
                    <span class={`medallion sev-${severity}`} aria-hidden="true">
                        {worst ? "!" : <MetricIcon name="check" size={20} />}
                    </span>
                    <span class="medallion-text">
                        <strong>{worst ? worst.title : "Nothing needs attention"}</strong>
                        <span class="small muted">{worst ? SEVERITY_WORD[worst.severity]
                            : `${passed} of ${checks.length} checks passed`}</span>
                    </span>
                </span>
                <span class="mini-gauges">
                    <span class="mini">
                        <ArcGauge percent={system.cpu} centre={`${system.cpu}%`} kind="accent" thin ticks />
                        <span class="mini-label">CPU</span>
                    </span>
                    <span class="mini">
                        <Gauge percent={memoryPercent} centre={`${memoryPercent}%`} size={96} stroke={11} />
                        <span class="mini-label">RAM</span>
                    </span>
                </span>
                <span class="mini-divider" />
                <span class="mini-heading">Pools</span>
                {!pools.length && <span class="small muted">No pool yet. Open Storage to create one.</span>}
                {pools.map(pool => {
                    const fill = pool.size ? Math.round((pool.allocated * 100) / pool.size) : 0;
                    const healthy = pool.health === "ONLINE";
                    return (
                        <span class="mini-pool" key={pool.name}>
                            <span class="mini-pool-name">{pool.name}</span>
                            <span class="bar"><span class={`fill ${level(fill)}`} style={{ width: `${fill}%` }} /></span>
                            <span class={`chip ${healthy ? "good" : "bad"}`}>
                                {healthy && <MetricIcon name="check" size={13} />}
                                {healthy ? "Healthy" : pool.health.toLowerCase()}
                            </span>
                        </span>
                    );
                })}
            </>}
        </button>
    );
}

type BayState = Severity | "unknown";

// Worst last, so the legend can name the drive that needs attention
const BAY_RANK: Record<BayState, number> = { good: 0, unknown: 1, low: 2, medium: 3, high: 4, critical: 5 };

/** A drive's level: whatever the checks said about it, or its SMART verdict. */
function bayState(disk: DiskHealth, problems: Problem[]): BayState {
    const problem = problems.find(item => item.key === `disk-${disk.path}`);
    if (problem) {
        return severityOf(problem);
    }
    if (disk.verdict === "unknown") {
        return "unknown";
    }
    return disk.verdict === "failing" ? "critical" : disk.verdict === "watch" ? "medium" : "good";
}

function bayWord(state: BayState) {
    return state === "unknown" ? "No health data" : state === "good" ? "Healthy" : SEVERITY_WORD[state];
}

/** The drive bays, one slot each, coloured by the drive's own level. */
function DisksWidget({ status, open }: { status: Status; open: (app: AppId) => void }) {
    const health = status.health;
    const problems = health?.problems || [];
    const disks = (health?.disks || []).filter(disk => disk.state !== "os");
    const states = disks.map(disk => bayState(disk, problems));
    const name = (disk: DiskHealth, index: number) => disk.bay || `Disk ${index + 1}`;
    const worst = disks.reduce<{ state: BayState; index: number } | null>((found, _, index) =>
        !found || BAY_RANK[states[index]] > BAY_RANK[found.state] ? { state: states[index], index } : found, null);
    return (
        <section class="widget disks-widget" aria-label="Disks">
            <button type="button" class="widget-head" onClick={() => open("health")}>
                <h2>Disks</h2>
                <span class="small muted">{plural(disks.length, "drive")}</span>
            </button>
            {!health && <p class="muted small">{status.error ? "Unavailable" : "Loading…"}</p>}
            {health && !disks.length && <p class="small muted">No drive reports itself yet.</p>}
            {health && disks.length > 0 && <>
                <div class="bays">
                    {disks.map((disk, index) => (
                        <div class="bay-cell" key={disk.path}>
                            <button type="button" class={`bay sev-${states[index]}`}
                                    title={`${name(disk, index)}: ${bayWord(states[index])}`}
                                    onClick={() => { focusDisk(disk.path); open("health"); }}>
                                <span class="bay-grille" aria-hidden="true"><span /><span /></span>
                                <span class="bay-mark" aria-hidden="true">
                                    {states[index] === "good" ? <MetricIcon name="check" size={18} />
                                        : states[index] === "unknown" ? "?" : "!"}
                                </span>
                                <span class="bay-dot" aria-hidden="true" />
                            </button>
                            <span class="bay-number">{disk.bay ? disk.bay.replace(/\D+/, "") : index + 1}</span>
                        </div>
                    ))}
                </div>
                <p class="bay-legend">
                    {worst && worst.state !== "good"
                        ? <>
                            <span class={`bay-dot sev-${worst.state}`} aria-hidden="true" />
                            <span>{name(disks[worst.index], worst.index)} · {bayWord(worst.state)}</span>
                        </>
                        : <span class="muted">All drives healthy</span>}
                </p>
            </>}
        </section>
    );
}

function Widgets({ status, open }: { status: Status; open: (app: AppId) => void }) {
    return (
        <aside class="widgets" aria-label="Status">
            <button type="button" class="widget" onClick={() => open("storage")}>
                <h2>Storage</h2>
                {!status.pools && <p class="muted small">{status.error || "Loading…"}</p>}
                {status.pools && !status.pools.length && <p class="small">No pools yet. Open Storage to create one.</p>}
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
            <HealthWidget status={status} open={open} />
            <DisksWidget status={status} open={open} />
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

/** Long jobs in progress, such as a disk being replaced, with how far along they are. */
function JobsIndicator({ open }: { open: (app: AppId) => void }) {
    const running = useJobs().filter(job => job.state === "running");
    if (!running.length) {
        return null;
    }
    const progress = Math.floor(running.reduce((sum, job) => sum + job.progress, 0) / running.length * 100);
    const titles = running.map(job => `${job.title}: ${Math.floor(job.progress * 100)}%`).join("\n");
    return (
        <button type="button" class="taskbar-button taskbar-jobs" title={titles} aria-label={titles} onClick={() => open("storage")}>
            <span class="taskbar-jobs-bar"><span style={{ width: `${progress}%` }} /></span>
            <span class="hide-narrow">{running.length === 1 ? running[0].title : `${running.length} jobs`}</span>
        </button>
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

function UserMenu({ open, onSignOut }: { open: (app: AppId) => void; onSignOut: () => void }) {
    const { user, admin } = useContext(SessionContext);
    const look = useAppearance();
    return (
        <div class="panel panel-right panel-small" role="menu" aria-label="Account">
            <p><strong>{user}</strong><br /><span class="small muted">{admin ? "Administrator" : "User"}</span></p>
            <div class="segmented" role="group" aria-label="Light or dark">
                {MODES.map(option => (
                    <button key={option} type="button" class={look.mode === option ? "current" : ""} aria-pressed={look.mode === option}
                            onClick={() => look.setMode(option)}>{option === "system" ? "Auto" : option[0].toUpperCase() + option.slice(1)}</button>
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
    const windows = useWindows(session.user, appsFor(session.admin));
    const status = useStatus(session.admin);
    const [panel, setPanel] = useState<Panel>(null);
    const [contextMenu, setContextMenu] = useState<{
        app?: AppId;
        x: number;
        y: number;
        trigger: HTMLElement;
    } | null>(null);
    const menuRef = useRef<HTMLDivElement>(null);
    // Until it is known, Setup's icon stays hidden rather than flashing up
    const [setupDone, setSetupDone] = useState(true);
    const apps = iconsFor(session.admin, setupDone);
    const found = problems(status);

    // Setup opens by itself for an admin until it has been finished or skipped
    useEffect(() => {
        if (!session.admin) {
            return;
        }
        fnasJson<SetupStatus>(["setup", "status"], true).then(setup => {
            setSetupDone(setup.done);
            if (!setup.done) {
                windows.open("setup");
            }
        }, () => {});
    }, [session.admin]);

    useEffect(() => {
        const escape = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                contextMenu?.trigger.focus();
                setContextMenu(null);
                setPanel(null);
            }
        };
        window.addEventListener("keydown", escape);
        return () => window.removeEventListener("keydown", escape);
    }, [contextMenu]);

    useLayoutEffect(() => {
        const menu = menuRef.current;
        if (!contextMenu || !menu) return;
        const inset = 8;
        menu.style.left = `${Math.max(inset, Math.min(contextMenu.x, window.innerWidth - menu.offsetWidth - inset))}px`;
        menu.style.top = `${Math.max(inset, Math.min(contextMenu.y, window.innerHeight - menu.offsetHeight - inset))}px`;
        menu.querySelector("button")?.focus();
    }, [contextMenu]);

    const showContextMenu = (event: MouseEvent, trigger: HTMLElement, app?: AppId) => {
        event.preventDefault();
        const anchor = trigger.getBoundingClientRect();
        const keyboard = event.clientX === 0 && event.clientY === 0;
        setPanel(null);
        setContextMenu({ app, trigger, x: keyboard ? anchor.left : event.clientX,
            y: keyboard ? anchor.top : event.clientY });
    };

    const open = (app: AppId) => {
        setContextMenu(null);
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

    const actions = {
        open,
        close: (app: AppId) => windows.windows.filter(win => win.app === app).forEach(win => windows.close(win.id)),
        setupFinished: () => setSetupDone(true),
    };

    return (
        <DesktopContext.Provider value={actions}>
        <div class="desktop"
             onContextMenu={event => {
                 if (!event.defaultPrevented) {
                     event.preventDefault();
                     setContextMenu(null);
                 }
             }}
             onPointerDown={event => {
                 if (!menuRef.current?.contains(event.target as Node)) setContextMenu(null);
             }}>
            <main class="desktop-area" tabIndex={-1}
                  onContextMenu={event => {
                      if (event.target === event.currentTarget) showContextMenu(event, event.currentTarget);
                  }}
                  onPointerDown={event => event.target === event.currentTarget && setPanel(null)}>
                <nav class="desktop-icons" aria-label="Apps">
                    {apps.map(app => (
                        <button type="button" key={app} class="desktop-icon" onClick={() => open(app)}
                                onContextMenu={event => showContextMenu(event, event.currentTarget, app)}>
                            <AppIcon name={app} size={52} />
                            <span>{APPS[app].label || APPS[app].title}</span>
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
            {contextMenu && (
                <div class="panel desktop-context-menu" ref={menuRef} role="group" aria-label="Desktop actions">
                    <button type="button" class="menu-item" onClick={() => open(contextMenu.app ?? "files")}>
                        Open {APPS[contextMenu.app ?? "files"].title}
                    </button>
                    {!contextMenu.app && (
                        <button type="button" class="menu-item" onClick={() => {
                            setContextMenu(null);
                            setPanel("launcher");
                        }}>
                            Show apps
                        </button>
                    )}
                </div>
            )}
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
                {session.admin && <JobsIndicator open={open} />}
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
        </DesktopContext.Provider>
    );
}
