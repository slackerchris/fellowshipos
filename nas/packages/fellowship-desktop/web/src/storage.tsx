import { Fragment } from "preact";
import { useState } from "preact/hooks";
import { Disk, Pool, diskName, fnas, fnasJson, humanSize } from "./api";
import { CreatePool, ImportPools } from "./pools";
import { Badge, Button, Card, Confirm, Loading, Page, Result, Usage, mount, useAction, useLoad } from "./ui";

function healthBadge(health: string) {
    const kind = health === "ONLINE" ? "good" : health === "DEGRADED" ? "warning" : "bad";
    return <Badge kind={kind}>{health === "ONLINE" ? "Healthy" : health.toLowerCase()}</Badge>;
}

const DISK_STATE = {
    os: "System disk",
    "in-use": "In use",
    "has-data": "Has old data",
    empty: "Empty",
} as const;

function Pools({ pools, reload }: { pools: Pool[]; reload: () => void }) {
    const [status, setStatus] = useState<Record<string, string>>({});
    const [exporting, setExporting] = useState<string | null>(null);
    const action = useAction(reload);
    const exportAction = useAction(reload);

    const toggleStatus = (name: string) => action.run(async () => {
        if (status[name]) {
            setStatus(({ [name]: _, ...rest }) => rest);
        } else {
            const text = await fnas(["pool", "status", name]);
            setStatus(previous => ({ ...previous, [name]: text }));
        }
    });

    if (!pools.length) {
        return <Card title="Pools"><p class="muted">No pools yet. Create one below, or import one from attached disks.</p></Card>;
    }
    return (
        <Card title="Pools">
            <div class="table-wrap">
                <table>
                    <thead><tr><th>Name</th><th>Health</th><th>Space</th><th /></tr></thead>
                    <tbody>
                        {pools.map(pool => (
                            <Fragment key={pool.name}>
                                <tr>
                                    <td><strong>{pool.name}</strong>{pool.readonly === "on" && <> <Badge kind="neutral">read-only</Badge></>}</td>
                                    <td>{healthBadge(pool.health)}</td>
                                    <td>
                                        <Usage used={pool.allocated} total={pool.size} />
                                        <span class="small muted">{humanSize(pool.allocated)} used of {humanSize(pool.size)}</span>
                                    </td>
                                    <td class="actions">
                                        <div class="row row-end">
                                            <Button onClick={() => toggleStatus(pool.name)}>{status[pool.name] ? "Hide details" : "Details"}</Button>
                                            {pool.readonly === "off" && (
                                                <Button busy={action.busy} onClick={() => action.run(() => fnas(["pool", "scrub", pool.name]))}>Check now</Button>
                                            )}
                                            <Button onClick={() => setExporting(pool.name)}>Export</Button>
                                        </div>
                                    </td>
                                </tr>
                                {status[pool.name] && (
                                    <tr><td colSpan={4}><pre class="mono small">{status[pool.name]}</pre></td></tr>
                                )}
                            </Fragment>
                        ))}
                    </tbody>
                </table>
            </div>
            <Result outcome={action.outcome} />
            <Result outcome={exportAction.outcome} />
            {exporting && (
                <Confirm title="Export pool" phrase={exporting} busy={exportAction.busy} outcome={exportAction.outcome}
                         onCancel={() => { setExporting(null); exportAction.clear(); }}
                         onConfirm={() => exportAction.run(async () => {
                             const output = await fnas(["pool", "export", exporting]);
                             setExporting(null);
                             return output;
                         })}>
                    <p>Its shares stop and the pool is closed so its disks can be moved. Nothing is erased, and it can be imported again.</p>
                </Confirm>
            )}
        </Card>
    );
}

function Disks({ disks }: { disks: Disk[] }) {
    return (
        <Card title="Disks">
            <div class="table-wrap">
                <table>
                    <thead><tr><th>Disk</th><th>Size</th><th>State</th></tr></thead>
                    <tbody>
                        {disks.map(disk => (
                            <tr key={disk.path}>
                                <td>{disk.model || "Disk"}<br /><span class="mono small muted">{diskName(disk)}</span></td>
                                <td>{humanSize(disk.size)}</td>
                                <td>{DISK_STATE[disk.state]}{disk.reason && <><br /><span class="small muted">{disk.reason}</span></>}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </Card>
    );
}

function Storage() {
    const pools = useLoad(() => fnasJson<Pool[]>(["pool", "list"]));
    const disks = useLoad(() => fnasJson<Disk[]>(["disk", "list", "--all"]));
    const [creating, setCreating] = useState(false);
    const [created, setCreated] = useState("");
    const reload = () => { pools.reload(); disks.reload(); };

    return (
        <Page title="Storage" intro="Pools hold your shares. Each pool is made from one or more disks.">
            <Loading loaded={pools}>{() => <Pools pools={pools.data!} reload={reload} />}</Loading>
            {created && <Result outcome={{ kind: "done", text: `Created pool ${created}` }} />}
            {creating
                ? <CreatePool taken={(pools.data || []).map(pool => pool.name)}
                              onCreated={name => { setCreating(false); setCreated(name); reload(); }} />
                : <div class="row"><Button kind="primary" onClick={() => { setCreating(true); setCreated(""); }}>Create a pool</Button></div>}
            <ImportPools onChange={reload} />
            <Loading loaded={disks}>{() => <Disks disks={disks.data!} />}</Loading>
        </Page>
    );
}

mount(Storage);
