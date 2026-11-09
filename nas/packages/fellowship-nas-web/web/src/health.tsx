import { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { Check, Health as HealthData, PoolDetail, Problem, Projection, diskName, fnasJson, humanSize, plural, when } from "./api";
import { STATE_BADGE } from "./sharing";
import { Badge, Button, Card, Loading, Page, Usage, useLoad } from "./ui";
import { ArcGauge, Dial, DotChart, Gauge, Kind, MetricIcon, NotEnoughHistory, RangePicker, TrendChart } from "./charts";
import { useDiskFocus } from "./desktop/focus";

const VERDICT = {
    healthy: { kind: "good", text: "Healthy" },
    watch: { kind: "warning", text: "Keep an eye on it" },
    failing: { kind: "bad", text: "Failing" },
    unknown: { kind: "neutral", text: "No health data" },
} as const;

export const SEVERITY = {
    info: "neutral", low: "neutral", medium: "warning", high: "warning", critical: "bad", warning: "warning",
} as const;

/** How a level reads when written out beside its colour. */
export const SEVERITY_WORD: Record<string, string> = {
    info: "Information", low: "Low warning", medium: "Medium warning", high: "High warning",
    critical: "Critical", warning: "Medium warning",
};

/** The five-level scale the medallion and the banner share: green, yellow,
    orange, red, purple, worst last. */
export type Severity = "good" | "low" | "medium" | "high" | "critical";

export function severityOf(problem?: Problem): Severity {
    if (!problem || problem.severity === "info") {
        return "good";
    }
    if (problem.severity === "low") {
        return "low";
    }
    if (problem.severity === "medium" || problem.severity === "warning") {
        return "medium";
    }
    return problem.severity === "high" ? "high" : "critical";
}

const CHECK_BADGE = {
    passed: { kind: "good", text: "Passed" },
    low: { kind: "neutral", text: "Low" },
    medium: { kind: "warning", text: "Medium" },
    high: { kind: "warning", text: "High" },
    critical: { kind: "bad", text: "Critical: broken" },
    na: { kind: "neutral", text: "Doesn't apply yet" },
} as const;

/** How a service's state reads on one line. */
const STATE_TEXT: Record<string, string> = {
    running: "Running", stopped: "Stopped", failed: "Failed", missing: "Not installed",
    off: "Turned off", "not-built": "Not built yet", later: "Planned",
};

function duration(seconds: number) {
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor(seconds % 86400 / 3600);
    return days ? `${plural(days, "day")}, ${plural(hours, "hour")}`
        : hours ? plural(hours, "hour") : plural(Math.floor(seconds / 60), "minute");
}

/** A share of a whole, as a number a gauge can show. */
function percent(used: number, total: number) {
    return total ? Math.round((used * 100) / total) : 0;
}

/** Fine, worth watching, or past the point of ignoring. */
export function level(value: number, warning = 80, bad = 90): Kind {
    return value >= bad ? "bad" : value >= warning ? "warning" : "good";
}

/** When a pool fills, in words, or why nothing can be said yet. */
function projectionText(projection: Projection) {
    if (projection.days === undefined) {
        return projection.reason || "No estimate yet";
    }
    return projection.days > 3650 ? "Full in over 10 years" : `Full in ${plural(projection.days, "day")}`;
}

const SECTION_VIEWS: Record<string, View> = {
    Pools: "pools", Protection: "checks", System: "system", Services: "services", Disks: "disks",
};

/** The area a problem belongs to, from the check that found it. */
function viewOf(health: HealthData, key: string): View {
    const check = health.checks.find(item => item.problems.some(problem => problem.key === key));
    return (check && SECTION_VIEWS[check.section]) || "checks";
}

/** The one line at the top: either the problem that needs you, or the all-clear. */
function Banner({ health, open }: { health: HealthData; open: (view: View) => void }) {
    const [worst] = health.problems;
    const passed = health.checks.filter(item => item.state === "passed").length;
    const waiting = health.checks.filter(item => item.state === "na").length;
    const severity = severityOf(worst);
    const checked = new Date(health.time * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    const body = (
        <>
            <span class="banner-icon"><MetricIcon name={worst ? "bell" : "check"} size={26} /></span>
            <span class="banner-text">
                <strong>{worst ? worst.title : "Nothing needs attention"}</strong>
                <span class="small">{worst ? worst.message
                    : `${passed} of ${health.checks.length} checks passed · ${waiting} not applicable`}</span>
            </span>
            <span class="banner-when small">{worst
                ? `${plural(health.problems.length, "problem")} to look at` : `Last checked ${checked}`}</span>
        </>
    );
    return worst
        ? <button type="button" class={`banner sev-${severity}`}
                  onClick={() => open(viewOf(health, worst.key))}>{body}</button>
        : <div class={`banner sev-${severity}`}>{body}</div>;
}

/** One card in a group: an icon and a name, the reading, and a way in. */
function Metric({ icon, title, onOpen, children }: {
    icon: "cpu" | "memory" | "load" | "pools" | "disk" | "services";
    title: string;
    onOpen: () => void;
    children: ComponentChildren;
}) {
    return (
        <section class="metric">
            <header class="metric-head"><MetricIcon name={icon} /><h3>{title}</h3></header>
            <div class="metric-body">{children}</div>
            <button type="button" class="metric-more" onClick={onOpen}>View details →</button>
        </section>
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
    const down = (state: string) => health.services.filter(service => service.state === state).length;
    const broken = down("failed") + health.services.filter(service => service.state === "stopped"
        && service.unit !== "smartmontools.service").length;
    const running = down("running");
    return <>
        <div class="gauges">
            <Dial parts={[{ value: running, kind: "good" }, { value: broken, kind: "bad" },
                          { value: down("off"), kind: "neutral" },
                          { value: health.services.length - running - broken - down("off"), kind: "warning" }]}
                  centre={`${running}/${health.services.length}`} label="Running"
                  note={broken ? `${broken} not running` : "Everything the NAS needs"} />
        </div>
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
    </>;
}

function System({ health }: { health: HealthData }) {
    const system = health.system;
    const [range, setRange] = useState<"hourly" | "daily">("hourly");
    const memoryUsed = system.memory.total - system.memory.available;
    const cores = system.cpus || 1;
    const points = health.history.system[range];
    // A chart of one number from each sample. Sensors that report nothing are left out.
    const pick = (index: number) => points
        .filter(point => point[index] !== null)
        .map(point => [point[0], point[index] as number] as [number, number]);
    const temperature = system.temperature;
    return <>
        <div class="gauges">
            <Gauge percent={percent(system.disk.used, system.disk.size)} centre={`${percent(system.disk.used, system.disk.size)}%`}
                   label="System disk" note={`${humanSize(system.disk.used)} of ${humanSize(system.disk.size)}`}
                   kind={level(percent(system.disk.used, system.disk.size), 85, 95)} />
            <Gauge percent={percent(memoryUsed, system.memory.total)} centre={`${percent(memoryUsed, system.memory.total)}%`}
                   label="Memory" note={`${humanSize(memoryUsed)} of ${humanSize(system.memory.total)}`}
                   kind={level(percent(memoryUsed, system.memory.total))} />
            <Gauge percent={Math.min(100, Math.round(system.load[0] / cores * 100))} centre={system.load[0].toFixed(2)}
                   label="Load" note={`${plural(cores, "processor")}, over 1 minute`}
                   kind={level(Math.round(system.load[0] / cores * 100), 70, 100)} />
            <Gauge percent={temperature === null ? 0 : Math.min(100, (temperature / 80) * 100)}
                   centre={temperature === null ? "–" : `${Math.round(temperature)}°`} label="Temperature"
                   note={temperature === null ? "No sensor reports it" : "Hottest sensor"}
                   kind={temperature === null ? "neutral" : level(temperature, 65, 80)} />
        </div>
        <Card title="Over time" actions={<RangePicker range={range} setRange={setRange} />}>
            {points.length < 2
                ? <NotEnoughHistory what="memory, load, temperature and the system disk" unit="once an hour" />
                : <div class="charts">
                    <div class="chart-block">
                        <h3>Memory in use</h3>
                        <TrendChart points={pick(1)} hours={range === "hourly"} format={humanSize} label="Memory in use"
                                    max={system.memory.total} guide={system.memory.total} />
                    </div>
                    <div class="chart-block">
                        <h3>Load, over 1 minute</h3>
                        <TrendChart points={pick(3)} hours={range === "hourly"} format={value => value.toFixed(2)} label="Load"
                                    max={cores} guide={cores} kind="warning" />
                        <p class="small muted">The dashed line is every processor busy.</p>
                    </div>
                    <div class="chart-block">
                        <h3>Temperature</h3>
                        {pick(6).length < 2
                            ? <p class="muted small">No sensor reports a temperature here, as in most virtual machines.</p>
                            : <TrendChart points={pick(6)} hours={range === "hourly"} format={value => `${Math.round(value)} °C`}
                                          label="Temperature" max={100} guide={80} kind="bad" />}
                    </div>
                </div>}
        </Card>
        <Card title="Right now">
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
    </>;
}

/** One pool: how full it is, how that has changed, and what protects it. */
function PoolCard({ pool, range }: { pool: PoolDetail; range: "hourly" | "daily" }) {
    const points = pool.history[range];
    const fill = percent(pool.allocated, pool.size);
    const protection = `Last scrub: ${pool.last_scrub ? when(pool.last_scrub) : "never"} · `
        + `Last snapshot: ${pool.last_snapshot ? when(pool.last_snapshot) : "none yet"} · `
        + "Backup: " + (!pool.backup ? "none"
            : pool.backup.error ? `failed (${pool.backup.error})`
            : `to ${pool.backup.target}, last ${pool.backup.last ? when(pool.backup.last) : "not run yet"}`);
    return (
        <Card title={pool.name} actions={
            <Badge kind={pool.health === "ONLINE" ? "good" : pool.health === "DEGRADED" ? "warning" : "bad"}>
                {pool.health === "ONLINE" ? "healthy" : pool.health.toLowerCase()}</Badge>}>
            <div class="stats">
                <div class="stat"><span class="small muted">Full</span><strong>{fill}%</strong>
                    <span class="small muted">{pool.engine.toUpperCase()}, {plural(pool.shares, "share")}
                        {pool.readonly ? ", read-only" : ""}</span></div>
                <div class="stat"><span class="small muted">Free</span><strong>{humanSize(pool.free)}</strong>
                    <span class="small muted">of {humanSize(pool.size)}</span></div>
                <div class="stat"><span class="small muted">{pool.projection.days === undefined ? "Fills up" : "Fills up in"}</span>
                    <strong>{pool.projection.days === undefined ? "–"
                        : pool.projection.days > 3650 ? "10+ years" : plural(pool.projection.days, "day")}</strong>
                    <span class="small muted">{pool.projection.days === undefined ? pool.projection.reason
                        : `at ${humanSize(pool.projection.per_day!)} a day`}</span></div>
            </div>
            {points.length < 2
                ? <NotEnoughHistory what={`how full ${pool.name} is`} unit={range === "hourly" ? "once an hour" : "once a day"} />
                : <TrendChart points={points.map(point => [point[0], point[1]] as [number, number])}
                              hours={range === "hourly"} format={humanSize} label={`Space used in ${pool.name}`}
                              max={pool.size} guide={pool.size} />}
            <p class="small muted">{protection}</p>
        </Card>
    );
}

function Pools({ health }: { health: HealthData }) {
    const [range, setRange] = useState<"hourly" | "daily">("hourly");
    if (!health.pools.length) {
        return <Card title="Pools"><p class="muted">No pool yet. Create or import one in Storage.</p></Card>;
    }
    return <>
        <div class="row">
            <p class="muted small">How full each pool has been, recorded by the hourly check.</p>
            <RangePicker range={range} setRange={setRange} />
        </div>
        {health.pools.map(pool => <PoolCard key={pool.name} pool={pool} range={range} />)}
    </>;
}

function Disks({ health }: { health: HealthData }) {
    const count = (verdict: string) => health.disks.filter(disk => disk.verdict === verdict).length;
    const unknown = count("unknown");
    // A widget can ask for one drive, so its row is marked and brought into view
    const focused = useDiskFocus();
    const focusedRow = useRef<HTMLTableRowElement>(null);
    useEffect(() => { focusedRow.current?.scrollIntoView({ block: "nearest" }); }, [focused]);
    return <>
        <div class="gauges">
            <Dial parts={[{ value: count("healthy"), kind: "good" }, { value: count("watch"), kind: "warning" },
                          { value: count("failing"), kind: "bad" }, { value: unknown, kind: "neutral" }]}
                  centre={`${count("healthy")}/${health.disks.length}`} label="Healthy"
                  note={unknown === health.disks.length ? "No disk reports health"
                      : unknown ? `${unknown} without health data` : "Every disk reports"} />
        </div>
        <Card title="Disks">
            <div class="table-wrap">
                <table>
                    <thead><tr><th>Disk</th><th>Health</th><th>Temperature</th><th>Powered on</th></tr></thead>
                    <tbody>
                        {health.disks.map(disk => (
                            <tr key={disk.path} class={disk.path === focused ? "row-focus" : undefined}
                                ref={disk.path === focused ? focusedRow : undefined}>
                                <td>
                                    {humanSize(disk.size)} {disk.model || "Disk"}{disk.state === "os" ? " (system)" : ""}
                                    {disk.bay && <span class="muted small"> · {disk.bay}</span>}
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
    </>;
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

/** The whole NAS at a glance: the verdict, then the system, then storage and
    the services that serve it. Each card opens its own details. */
function Overview({ health, open }: { health: HealthData; open: (view: View) => void }) {
    const system = health.system;
    const memoryUsed = system.memory.total - system.memory.available;
    const memoryPercent = percent(memoryUsed, system.memory.total);
    const cores = system.cpus || 1;
    const load = system.load[0];
    // The hourly check records load once an hour, so the chart shows a day of it
    const loadPoints = health.history.system.hourly.slice(-24)
        .map(point => [point[0], point[3]] as [number, number]);
    const faulty = health.disks.filter(disk => disk.verdict === "failing").length;
    const watching = health.disks.filter(disk => disk.verdict === "watch").length;
    const unknown = health.disks.filter(disk => disk.verdict === "unknown").length;
    const sharing = health.protocols
        .filter(protocol => ["running", "stopped", "failed", "missing"].includes(protocol.state))
        .map(protocol => ({ name: protocol.title, state: protocol.state }));
    const ssh = health.services.find(service => service.unit === "ssh.service");
    const services = ssh ? [...sharing, { name: "SSH", state: ssh.state }] : sharing;
    const backup = health.pools.find(pool => pool.backup)?.backup ?? null;
    const backupText = !backup ? "No backup job"
        : backup.error ? "Last attempt failed"
            : backup.last ? "Last run succeeded" : "Not run yet";
    return (
        <>
            <Banner health={health} open={open} />
            <h2 class="group-title">System</h2>
            <div class="metrics">
                <Metric icon="cpu" title="CPU usage" onOpen={() => open("system")}>
                    <ArcGauge percent={system.cpu} centre={`${system.cpu}%`} kind={level(system.cpu, 80, 95)} />
                </Metric>
                <Metric icon="memory" title="Memory" onOpen={() => open("system")}>
                    <Gauge percent={memoryPercent} centre={`${memoryPercent}%`} size={128} stroke={14}
                           note={`${humanSize(memoryUsed)} of ${humanSize(system.memory.total)} used`}
                           kind={level(memoryPercent, 85, 95)} />
                </Metric>
                <Metric icon="load" title="CPU load" onOpen={() => open("system")}>
                    <div class="load-body">
                        {loadPoints.length > 1
                            ? <DotChart points={loadPoints} hours format={value => value.toFixed(1)} label="CPU load"
                                        max={cores} guide={cores} guideLabel={plural(cores, "logical CPU")} />
                            : <NotEnoughHistory what="the load" unit="once an hour" />}
                        <div class="load-now"><strong>{load.toFixed(2)}</strong>
                            <span class="small muted">Last 24 hours</span></div>
                    </div>
                </Metric>
            </div>
            <h2 class="group-title">Storage &amp; services</h2>
            <div class="metrics">
                <Metric icon="pools" title="Pools" onOpen={() => open("pools")}>
                    {!health.pools.length && <p class="small muted">No pool yet. Create one in Storage.</p>}
                    {health.pools.map(pool => {
                        const fill = percent(pool.allocated, pool.size);
                        return (
                            <div class="pool-line" key={pool.name}>
                                <span class="pool-name">{pool.name}</span>
                                <span class="bar"><span class={`fill ${level(fill)}`}
                                                        style={{ width: `${fill}%` }} /></span>
                                <span class="small muted">{humanSize(pool.allocated)} / {humanSize(pool.size)}</span>
                                <span class={`chip ${pool.health === "ONLINE" ? "good" : "bad"}`}>
                                    {pool.health === "ONLINE" && <MetricIcon name="check" size={14} />}
                                    {pool.health === "ONLINE" ? "Healthy" : pool.health.toLowerCase()}
                                </span>
                            </div>
                        );
                    })}
                </Metric>
                <Metric icon="disk" title="Disks" onOpen={() => open("disks")}>
                    <p class="small muted">{plural(health.disks.length, "drive")}</p>
                    <div class="drives">
                        {health.disks.slice(0, 6).map(disk => <MetricIcon key={disk.path} name="disk" size={30} />)}
                        {health.disks.length > 6 && <span class="small muted">+{health.disks.length - 6}</span>}
                    </div>
                    <p class="small muted">{faulty ? `${plural(faulty, "drive")} failing`
                        : unknown === health.disks.length ? "No SMART data"
                            : watching ? `${watching} to watch` : "All healthy"}</p>
                </Metric>
                <Metric icon="services" title="Services" onOpen={() => open("services")}>
                    {services.map(service => (
                        <div class="service-line" key={service.name}>
                            <span class={`state-dot ${service.state}`} />
                            <span>{service.name}</span>
                            <span class="state small">{STATE_TEXT[service.state] ?? service.state}</span>
                        </div>
                    ))}
                </Metric>
            </div>
            <div class="status-strip">
                <button type="button" onClick={() => open("pools")}>
                    <MetricIcon name="backup" /><strong>Backup</strong><span>{backupText}</span>
                </button>
                <button type="button" onClick={() => open("alerts")}>
                    <MetricIcon name="bell" /><strong>Alerts</strong>
                    <span>{health.problems.length
                        ? `${plural(health.problems.length, "alert")} needing attention` : "No active alerts"}</span>
                </button>
            </div>
        </>
    );
}

const DETAILS: Record<View, (props: { health: HealthData }) => preact.JSX.Element> = {
    checks: Checks, services: Services, system: System, pools: Pools, disks: Disks, alerts: Alerting,
};

export function Health() {
    const health = useLoad(() => fnasJson<HealthData>(["health"]));
    const [view, setView] = useState<View | null>(null);
    const focused = useDiskFocus();
    // A widget naming a drive opens straight at the disks
    useEffect(() => { if (focused) setView("disks"); }, [focused]);
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
                return <>
                    <div class="row row-end">
                        <Button onClick={health.reload}>Check again</Button>
                    </div>
                    <Overview health={data} open={setView} />
                </>;
            }}</Loading>
        </Page>
    );
}
