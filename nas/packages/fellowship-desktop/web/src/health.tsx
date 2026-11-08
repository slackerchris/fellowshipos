import { Alert, Health, Pool, diskName, fnasJson, humanSize, when } from "./api";
import { AlertSetup } from "./alerting";
import { Badge, Card, Loading, Notice, Page, Usage, mount, useLoad } from "./ui";

const VERDICT = {
    healthy: { kind: "good", text: "Healthy" },
    watch: { kind: "warning", text: "Keep an eye on it" },
    failing: { kind: "bad", text: "Failing" },
    unknown: { kind: "neutral", text: "No health data" },
} as const;

const SEVERITY = { info: "neutral", warning: "warning", critical: "bad" } as const;

function Problems({ health, pools }: { health: Health; pools: Pool[] }) {
    const unhealthy = pools.filter(pool => pool.health !== "ONLINE");
    if (!health.problems.length && !unhealthy.length) {
        return <Notice kind="done">Everything looks fine.</Notice>;
    }
    return (
        <Card title="Needs attention">
            {unhealthy.map(pool => (
                <div key={pool.name}>
                    <Badge kind="bad">{pool.health.toLowerCase()}</Badge> <strong>Pool {pool.name}</strong>
                    <p class="small muted">A disk is missing or failing. Storage → Details shows which.</p>
                </div>
            ))}
            {health.problems.map(problem => (
                <div key={problem.key}>
                    <Badge kind={SEVERITY[problem.severity]}>{problem.severity}</Badge> <strong>{problem.title}</strong>
                    <p class="small muted">{problem.message}</p>
                </div>
            ))}
        </Card>
    );
}

function Disks({ health }: { health: Health }) {
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

function Pools({ pools }: { pools: Pool[] }) {
    if (!pools.length) {
        return null;
    }
    return (
        <Card title="Space">
            {pools.map(pool => (
                <div key={pool.name} class="stack">
                    <div class="row"><strong>{pool.name}</strong>
                        <span class="small muted">{humanSize(pool.free)} free of {humanSize(pool.size)}</span></div>
                    <Usage used={pool.allocated} total={pool.size} />
                </div>
            ))}
        </Card>
    );
}

function RecentAlerts({ alerts }: { alerts: Alert[] }) {
    return (
        <Card title="Recent alerts">
            {!alerts.length && <p class="muted">None yet.</p>}
            {[...alerts].reverse().map(alert => (
                <div key={`${alert.time}-${alert.title}`}>
                    <Badge kind={SEVERITY[alert.severity]}>{alert.severity}</Badge> <strong>{alert.title}</strong>{" "}
                    <span class="small muted">{when(alert.time)}</span>
                    <p class="small muted">{alert.message}</p>
                    {alert.undelivered?.length > 0 && <p class="small">Not delivered: {alert.undelivered.join("; ")}</p>}
                </div>
            ))}
        </Card>
    );
}

function HealthPage() {
    const health = useLoad(() => fnasJson<Health>(["health"]));
    const pools = useLoad(() => fnasJson<Pool[]>(["pool", "list"]));
    const alerts = useLoad(() => fnasJson<Alert[]>(["alert", "list", "--limit", "10"]));
    return (
        <Page title="Health" intro="Disks, pools and the checks that run every hour.">
            <Loading loaded={health}>{() => <Loading loaded={pools}>{() => <>
                <Problems health={health.data!} pools={pools.data!} />
                <Pools pools={pools.data!} />
                <Disks health={health.data!} />
            </>}</Loading>}</Loading>
            <AlertSetup />
            <Loading loaded={alerts}>{() => <RecentAlerts alerts={alerts.data!} />}</Loading>
        </Page>
    );
}

mount(HealthPage);
