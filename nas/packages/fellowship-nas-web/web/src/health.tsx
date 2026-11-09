import { useState } from "preact/hooks";
import { Check, Health as HealthData, diskName, fnasJson, humanSize, plural, when } from "./api";
import { STATE_BADGE } from "./sharing";
import { Badge, Button, Card, Loading, Notice, Page, Usage, useLoad } from "./ui";

const VERDICT = {
    healthy: { kind: "good", text: "Healthy" },
    watch: { kind: "warning", text: "Keep an eye on it" },
    failing: { kind: "bad", text: "Failing" },
    unknown: { kind: "neutral", text: "No health data" },
} as const;

export const SEVERITY = {
    info: "neutral", low: "neutral", medium: "warning", high: "warning", critical: "bad", warning: "warning",
} as const;

const CHECK_BADGE = {
    passed: { kind: "good", text: "Passed" },
    low: { kind: "neutral", text: "Low" },
    medium: { kind: "warning", text: "Medium" },
    high: { kind: "warning", text: "High" },
    critical: { kind: "bad", text: "Critical: broken" },
    na: { kind: "neutral", text: "Doesn't apply yet" },
} as const;

function duration(seconds: number) {
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor(seconds % 86400 / 3600);
    return days ? `${plural(days, "day")}, ${plural(hours, "hour")}`
        : hours ? plural(hours, "hour") : plural(Math.floor(seconds / 60), "minute");
}

const RANK = { info: 0, low: 1, medium: 2, high: 3, warning: 2, critical: 4 } as const;

const SECTION_VIEWS: Record<string, View> = {
    Pools: "pools", Protection: "checks", System: "system", Services: "services", Disks: "disks",
};

/** The area a problem belongs to, from the check that found it. */
function viewOf(health: HealthData, key: string): View {
    const check = health.checks.find(item => item.problems.some(problem => problem.key === key));
    return (check && SECTION_VIEWS[check.section]) || "checks";
}

function Problems({ health, open }: { health: HealthData; open: (view: View) => void }) {
    const waiting = health.checks.filter(item => item.state === "na").length;
    if (!health.problems.length) {
        return waiting
            ? <Notice>Nothing needs attention. {plural(waiting, "check")} {waiting === 1 ? "doesn't" : "don't"} apply
                yet; Checks says why.</Notice>
            : <Notice kind="done">Every check passed.</Notice>;
    }
    return (
        <Card title="Needs attention">
            <p class="muted small">Critical means something is broken. High, medium and low are warnings, by how soon
                they need you.</p>
            {health.problems.map(problem => (
                <button type="button" class="problem" key={problem.key} onClick={() => open(viewOf(health, problem.key))}>
                    <Badge kind={SEVERITY[problem.severity]}>{problem.severity}</Badge> <strong>{problem.title}</strong>
                    <p class="small muted">{problem.message}</p>
                </button>
            ))}
        </Card>
    );
}

function Checks({ health }: { health: HealthData }) {
    const sections = [...new Set(health.checks.map(item => item.section))];
    const hourly = health.hourly.last;
    return (
        <Card title="Checks">
            <p class="muted small">Worked out {when(health.time)}. The hourly check, which sends alerts,
                {hourly ? ` last ran ${when(hourly)}.` : " hasn't run yet: it first runs 10 minutes after starting up."}</p>
            <div class="table-wrap">
                <table>
                    <thead><tr><th>Check</th><th>Result</th><th>Details</th></tr></thead>
                    {sections.map(section => (
                        <tbody key={section}>
                            <tr><th colSpan={3} class="section-row">{section}</th></tr>
                            {health.checks.filter((item: Check) => item.section === section).map(item => (
                                <tr key={item.key}>
                                    <td>{item.title}</td>
                                    <td><Badge kind={CHECK_BADGE[item.state].kind}>{CHECK_BADGE[item.state].text}</Badge></td>
                                    <td class="small">{item.detail}{!item.alerting && item.state !== "na" &&
                                        <span class="muted"> (smartd alerts about disks itself)</span>}</td>
                                </tr>
                            ))}
                        </tbody>
                    ))}
                </table>
            </div>
        </Card>
    );
}

function Services({ health }: { health: HealthData }) {
    return (
        <Card title="Services">
            <div class="table-wrap">
                <table>
                    <thead><tr><th>Service</th><th>What it does</th><th>State</th></tr></thead>
                    <tbody>
                        {health.protocols.map(protocol => (
                            <tr key={protocol.name}>
                                <td>Sharing over {protocol.title}</td>
                                <td class="small">{protocol.for}</td>
                                <td><Badge kind={STATE_BADGE[protocol.state]}>{protocol.detail}</Badge></td>
                            </tr>
                        ))}
                        {health.services.map(service => (
                            <tr key={service.unit}>
                                <td>{service.title}<br /><span class="small muted mono">{service.unit}</span></td>
                                <td class="small">{service.does}</td>
                                <td><Badge kind={STATE_BADGE[service.state]}>{service.detail}</Badge></td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </Card>
    );
}

function System({ health }: { health: HealthData }) {
    const system = health.system;
    const memoryUsed = system.memory.total - system.memory.available;
    return (
        <Card title="System">
            <div class="table-wrap">
                <table>
                    <tbody>
                        <tr><th scope="row">System disk</th><td>
                            <Usage used={system.disk.used} total={system.disk.size} />
                            <span class="small">{humanSize(system.disk.used)} used of {humanSize(system.disk.size)}</span></td></tr>
                        <tr><th scope="row">Memory</th><td>
                            <Usage used={memoryUsed} total={system.memory.total} />
                            <span class="small">{humanSize(memoryUsed)} in use of {humanSize(system.memory.total)}</span></td></tr>
                        <tr><th scope="row">Load</th><td class="small">{system.load.map(n => n.toFixed(2)).join(", ")} over 1, 5
                            and 15 minutes, on {plural(system.cpus, "processor")}</td></tr>
                        <tr><th scope="row">Temperature</th><td class="small">{system.temperature === null
                            ? "No sensor reports it, as in most virtual machines" : `${system.temperature.toFixed(0)} °C`}</td></tr>
                        <tr><th scope="row">Running for</th><td class="small">{duration(system.uptime)}</td></tr>
                        <tr><th scope="row">Updates</th><td class="small">{system.updates === null
                            ? "Couldn't ask apt" : system.updates === 0 ? "Up to date"
                            : `${plural(system.updates, "package")} can be updated`}
                            <span class="muted"> (as of the last package list refresh)</span></td></tr>
                        <tr><th scope="row">Restart needed</th><td class="small">{system.reboot ? "Yes, to finish installing updates" : "No"}</td></tr>
                        <tr><th scope="row">Kernel</th><td class="small mono">{system.kernel}
                            {system.newest_kernel && system.newest_kernel !== system.kernel &&
                                <span class="muted"> ({system.newest_kernel} starts at the next restart)</span>}</td></tr>
                    </tbody>
                </table>
            </div>
        </Card>
    );
}

function Pools({ health }: { health: HealthData }) {
    return (
        <Card title="Pools">
            {!health.pools.length && <p class="muted">No pool yet. Create or import one in Storage.</p>}
            {health.pools.map(pool => (
                <div key={pool.name} class="stack">
                    <div class="row">
                        <strong>{pool.name}</strong>
                        <Badge kind={pool.health === "ONLINE" ? "good" : pool.health === "DEGRADED" ? "warning" : "bad"}>
                            {pool.health === "ONLINE" ? "healthy" : pool.health.toLowerCase()}</Badge>
                        <span class="small muted">{pool.engine.toUpperCase()}, {plural(pool.shares, "share")}
                            {pool.readonly ? ", read-only" : ""}</span>
                    </div>
                    <Usage used={pool.allocated} total={pool.size} />
                    <span class="small">{humanSize(pool.free)} free of {humanSize(pool.size)}</span>
                    <span class="small">Last scrub: {pool.last_scrub ? when(pool.last_scrub) : "never"} ·
                        Last snapshot: {pool.last_snapshot ? when(pool.last_snapshot) : "none yet"} ·
                        Backup: {!pool.backup ? "none"
                            : pool.backup.error ? `failed (${pool.backup.error})`
                            : `to ${pool.backup.target}, last ${pool.backup.last ? when(pool.backup.last) : "not run yet"}`}</span>
                </div>
            ))}
        </Card>
    );
}

function Disks({ health }: { health: HealthData }) {
    return (
        <Card title="Disks">
            <div class="table-wrap">
                <table>
                    <thead><tr><th>Disk</th><th>Health</th><th>Temperature</th><th>Powered on</th></tr></thead>
                    <tbody>
                        {health.disks.map(disk => (
                            <tr key={disk.path}>
                                <td>
                                    {humanSize(disk.size)} {disk.model || "Disk"}{disk.state === "os" ? " (system)" : ""}
                                    <br /><span class="mono small muted">{diskName(disk)}</span>
                                </td>
                                <td>
                                    <Badge kind={VERDICT[disk.verdict].kind}>{VERDICT[disk.verdict].text}</Badge>
                                    {disk.notes.map(note => <div key={note} class="small">{note}</div>)}
                                </td>
                                <td>{disk.temperature === null ? "–" : `${disk.temperature} °C`}</td>
                                <td>{disk.hours === null ? "–" : `${Math.round(disk.hours / 24).toLocaleString()} days`}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </Card>
    );
}

function Alerting({ health }: { health: HealthData }) {
    const { channels, last } = health.alerts;
    const extra = [channels.email && `email to ${channels.email.to}`, channels.ntfy && `ntfy at ${channels.ntfy.url}`]
        .filter(Boolean);
    return (
        <Card title="Alerts">
            <p class="small">Alerts always appear in this desktop's notifications.
                {extra.length ? ` They are also sent by ${extra.join(" and ")}.` : " No other way of sending them is set up; Alerts can add email or ntfy."}</p>
            <p class="small">Last alert: {last ? <><strong>{last.title}</strong>, {when(last.time)}
                {last.undelivered.length > 0 && <span class="muted"> (not delivered: {last.undelivered.join("; ")})</span>}</> : "none yet"}</p>
        </Card>
    );
}

type View = "checks" | "services" | "system" | "pools" | "disks" | "alerts";

type Kind = "good" | "warning" | "bad" | "neutral";

interface Tile {
    view: View;
    title: string;
    badge: [Kind, string];
    lines: string[];
}

/** One short summary per area, each opening its details. */
function tiles(health: HealthData): Tile[] {
    const checks = health.checks;
    const passed = checks.filter(check => check.state === "passed").length;
    const waiting = checks.filter(check => check.state === "na").length;
    const worstCheck = checks.reduce((found, check) => check.state in RANK
        && RANK[check.state as keyof typeof RANK] > RANK[found as keyof typeof RANK] ? check.state : found, "info");
    const down = [...health.protocols.filter(p => (p.state === "stopped" || p.state === "failed") && p.shares.length),
                  ...health.services.filter(s => s.state === "failed"
                      || (s.state === "stopped" && s.unit !== "smartmontools.service"))];
    const running = health.services.filter(s => s.state === "running").length
        + health.protocols.filter(p => p.state === "running").length;
    const system = health.system;
    const diskUse = Math.round(system.disk.used * 100 / system.disk.size);
    const sick = health.pools.filter(pool => pool.health !== "ONLINE");
    const disks = health.disks;
    const failing = disks.filter(disk => disk.verdict === "failing").length;
    const watch = disks.filter(disk => disk.verdict === "watch").length;
    const unknown = disks.filter(disk => disk.verdict === "unknown").length;
    const { channels, last } = health.alerts;
    const ways = ["this desktop", channels.email && "email", channels.ntfy && "ntfy"].filter(Boolean);
    return [
        { view: "checks", title: "Checks",
          badge: worstCheck === "critical" ? ["bad", "critical"] : worstCheck !== "info" ? ["warning", worstCheck]
              : waiting ? ["neutral", "nothing wrong"] : ["good", "all passed"],
          lines: [`${passed} of ${checks.length} passed`, waiting ? `${waiting} don't apply yet` : ""] },
        { view: "services", title: "Services",
          badge: down.length ? ["bad", `${down.length} not running`] : ["good", "running"],
          lines: [down.length ? down.map(item => item.title).join(", ") : `${running} running`] },
        { view: "system", title: "System",
          badge: diskUse >= 85 || system.reboot ? ["warning", system.reboot ? "restart needed" : "disk filling"]
              : ["good", "fine"],
          lines: [`System disk ${diskUse}% full`,
                  system.updates ? `${plural(system.updates, "update")} waiting` : "Up to date"] },
        { view: "pools", title: "Pools",
          badge: !health.pools.length ? ["neutral", "none yet"] : sick.length ? ["bad", `${sick.length} not healthy`]
              : ["good", "healthy"],
          lines: [health.pools.length ? health.pools.map(pool => pool.name).join(", ") : "Create one in Storage"] },
        { view: "disks", title: "Disks",
          badge: failing ? ["bad", `${failing} failing`] : watch ? ["warning", `${watch} to watch`]
              : unknown === disks.length ? ["neutral", "no health data"] : ["good", "healthy"],
          lines: [plural(disks.length, "disk"), unknown && unknown < disks.length ? `${unknown} without health data` : ""] },
        { view: "alerts", title: "Alerts",
          badge: last?.undelivered.length ? ["warning", "not delivered"] : ["good", "set up"],
          lines: [`Sent to ${ways.join(" and ")}`, last ? `Last: ${when(last.time)}` : "None sent yet"] },
    ];
}

const DETAILS: Record<View, (props: { health: HealthData }) => preact.JSX.Element> = {
    checks: Checks, services: Services, system: System, pools: Pools, disks: Disks, alerts: Alerting,
};

export function Health() {
    const health = useLoad(() => fnasJson<HealthData>(["health"]));
    const [view, setView] = useState<View | null>(null);
    return (
        <Page>
            <Loading loaded={health}>{() => {
                const data = health.data!;
                if (view) {
                    const Details = DETAILS[view];
                    return <>
                        <div class="row">
                            <Button onClick={() => setView(null)}>← Health</Button>
                            <Button onClick={health.reload}>Check again</Button>
                        </div>
                        <Details health={data} />
                    </>;
                }
                const all = tiles(data);
                return <>
                    <div class="row">
                        <p class="muted small">Checked {when(data.time)}. Click an area for its details.</p>
                        <Button onClick={health.reload}>Check again</Button>
                    </div>
                    <Problems health={data} open={setView} />
                    <div class="tiles">
                        {all.map(tile => (
                            <button type="button" key={tile.view} class="tile" onClick={() => setView(tile.view)}>
                                <span class="row"><strong>{tile.title}</strong>
                                    <Badge kind={tile.badge[0]}>{tile.badge[1]}</Badge></span>
                                {tile.lines.filter(Boolean).map(line => <span key={line} class="small muted">{line}</span>)}
                            </button>
                        ))}
                    </div>
                </>;
            }}</Loading>
        </Page>
    );
}
