import { Health as HealthData, Pool, diskName, fnasJson, humanSize } from "./api";
import { Badge, Card, Loading, Notice, Page, Usage, useLoad } from "./ui";

const VERDICT = {
    healthy: { kind: "good", text: "Healthy" },
    watch: { kind: "warning", text: "Keep an eye on it" },
    failing: { kind: "bad", text: "Failing" },
    unknown: { kind: "neutral", text: "No health data" },
} as const;

export const SEVERITY = { info: "neutral", warning: "warning", critical: "bad" } as const;

function Problems({ health, pools }: { health: HealthData; pools: Pool[] }) {
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

export function Health() {
    const health = useLoad(() => fnasJson<HealthData>(["health"]));
    const pools = useLoad(() => fnasJson<Pool[]>(["pool", "list"]));
    return (
        <Page intro="Disks, pools and the checks that run every hour.">
            <Loading loaded={health}>{() => <Loading loaded={pools}>{() => <>
                <Problems health={health.data!} pools={pools.data!} />
                <Pools pools={pools.data!} />
                <Disks health={health.data!} />
            </>}</Loading>}</Loading>
        </Page>
    );
}

