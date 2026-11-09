// Snapshots of each share, the nightly backup to another pool, and restoring from it
import { useState } from "preact/hooks";
import { BackupCopy, BackupStatus, Policy, Pool, Share, Snapshot, fnas, fnasJson, plural, when } from "./api";
import { Badge, Button, Card, Field, Loading, Notice, Page, Result, useAction, useLoad } from "./ui";

const PERIODS: (keyof Policy)[] = ["hourly", "daily", "monthly"];

export function PolicyEditor({ share, onDone }: { share: Share; onDone: () => void }) {
    const [policy, setPolicy] = useState<Policy>(share.policy);
    const action = useAction(onDone);
    return (
        <form class="stack" onSubmit={event => {
            event.preventDefault();
            action.run(() => fnas(["snapshot", "policy", share.name,
                                   ...PERIODS.flatMap(period => [`--${period}`, String(policy[period])])]));
        }}>
            <div class="grid-2">
                {PERIODS.map(period => (
                    <Field key={period} label={`${period[0].toUpperCase()}${period.slice(1)} snapshots kept`}>
                        <input type="number" min={0} max={1000} value={policy[period]}
                               onInput={event => setPolicy({ ...policy, [period]: Number(event.currentTarget.value) })} />
                    </Field>
                ))}
            </div>
            <p class="muted small">Snapshots keep earlier versions of files. Set all three to 0 to stop taking them.</p>
            <div class="row">
                <Button kind="primary" type="submit" busy={action.busy}>Save</Button>
                <Button onClick={onDone}>Cancel</Button>
            </div>
            <Result outcome={action.outcome} />
        </form>
    );
}

/** The nightly backup: what it copies where, how the last run went, and setting it up. */
export function BackupJob({ onChange }: { onChange?: () => void }) {
    const pools = useLoad(() => fnasJson<Pool[]>(["pool", "list"]));
    const status = useLoad(() => fnasJson<BackupStatus>(["backup", "show"]));
    const [source, setSource] = useState("");
    const [target, setTarget] = useState("");
    const action = useAction(() => { status.reload(); onChange?.(); });
    return (
        <Card title="Nightly backup">
            <p class="muted small">Snapshots protect against deleted or changed files, not against losing a pool. A backup
                copies every share, with its snapshots, to a second pool, such as a USB disk, every night at 03:00.</p>
            <Loading loaded={pools}>{() => <Loading loaded={status}>{() => {
                const job = status.data!.job;
                if (job) {
                    const { last_success: last, error } = status.data!;
                    return (
                        <div class="stack">
                            <p>Shares on <strong>{job.source}</strong> are backed up to <strong>{job.target}</strong> every night.</p>
                            <p class="small">Last backup: {last ? when(last) : "not run yet"}
                                {error && <> · <Badge kind="bad">Last attempt failed</Badge> {error}</>}</p>
                            <div class="row">
                                <Button kind="primary" busy={action.busy}
                                        onClick={() => action.run(() => fnas(["backup", "run", "--background"]))}>Back up now</Button>
                                <Button busy={action.busy} onClick={() => action.run(() => fnas(["backup", "off"]))}>
                                    Stop backing up</Button>
                            </div>
                            <p class="muted small">Stopping keeps everything already on {job.target}.</p>
                            <Result outcome={action.outcome} />
                        </div>
                    );
                }
                const writable = pools.data!.filter(pool => !pool.readonly && pool.engine === "zfs").map(pool => pool.name);
                const from = source || writable[0] || "";
                const to = target || writable.find(pool => pool !== from) || "";
                const missing = writable.length < 2;
                return (
                    <form class="stack" onSubmit={event => {
                        event.preventDefault();
                        action.run(() => fnas(["backup", "set", from, to]));
                    }}>
                        {missing && <Notice>No backup is set up. A backup needs two pools, and this NAS has
                            {writable.length ? " one" : " none"}. Add a pool in Storage, then choose it here.</Notice>}
                        <fieldset class="plain" disabled={missing}>
                            <div class="grid-2">
                                <Field label="Back up shares on">
                                    <select value={from} onChange={event => setSource(event.currentTarget.value)}>
                                        {writable.map(pool => <option key={pool}>{pool}</option>)}
                                    </select>
                                </Field>
                                <Field label="To">
                                    <select value={to} onChange={event => setTarget(event.currentTarget.value)}>
                                        {writable.filter(pool => pool !== from).map(pool => <option key={pool}>{pool}</option>)}
                                    </select>
                                </Field>
                            </div>
                            <div class="row"><Button kind="primary" type="submit" busy={action.busy}>Back up every night</Button></div>
                        </fieldset>
                        <Result outcome={action.outcome} />
                    </form>
                );
            }}</Loading>}</Loading>
        </Card>
    );
}

function SnapshotSchedules() {
    const shares = useLoad(async () => {
        const found = await fnasJson<Share[]>(["share", "list"]);
        const taken = await Promise.all(found.map(share => fnasJson<Snapshot[]>(["snapshot", "list", share.name])
            .catch(() => [] as Snapshot[])));
        return found.map((share, index) => ({ share, snapshots: taken[index] }));
    });
    const [editing, setEditing] = useState<string | null>(null);
    return (
        <Card title="Snapshots">
            <p class="muted small">Each share keeps earlier versions of its files. Windows shows them as Previous Versions on
                SMB shares.</p>
            <Loading loaded={shares}>{() => (
                <div class="stack">
                    {!shares.data!.length && <p class="muted">No shares yet, so nothing to take snapshots of. Create one in
                        Shares.</p>}
                    {shares.data!.length > 0 && (
                        <div class="table-wrap">
                            <table>
                                <thead><tr><th>Share</th><th>Kept</th><th>Snapshots now</th><th /></tr></thead>
                                <tbody>
                                    {shares.data!.map(({ share, snapshots }) => (
                                        <tr key={share.dataset}>
                                            <td><strong>{share.name}</strong><br /><span class="small muted">on {share.pool}</span></td>
                                            <td>{editing === share.name
                                                ? <PolicyEditor share={share} onDone={() => { setEditing(null); shares.reload(); }} />
                                                : <span class="small">{share.policy.hourly} hourly, {share.policy.daily} daily,
                                                    {" "}{share.policy.monthly} monthly</span>}</td>
                                            <td class="small">{snapshots.length
                                                ? <>{plural(snapshots.length, "snapshot")}, newest {when(snapshots[snapshots.length - 1].created)}</>
                                                : "None yet"}</td>
                                            <td class="actions">{!share.readonly && editing !== share.name && (
                                                <Button onClick={() => setEditing(share.name)}>Change</Button>)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                    <Notice>Browsing snapshots and restoring single files here isn't built yet: it comes with the Files app
                        (Phase 13). Until then, use Previous Versions in Windows, or the snapshot folders over SSH.</Notice>
                </div>
            )}</Loading>
        </Card>
    );
}

function RestoreShares() {
    const status = useLoad(() => fnasJson<BackupStatus>(["backup", "show"]));
    const copies = useLoad(() => fnasJson<BackupCopy[]>(["backup", "list"]).catch(() => [] as BackupCopy[]));
    const shares = useLoad(() => fnasJson<Share[]>(["share", "list"]));
    const action = useAction(() => { copies.reload(); shares.reload(); });
    return (
        <Card title="Restore a share from the backup">
            <p class="muted small">Brings back a share that was deleted or lost with its pool, from the backup pool. A
                restore never replaces a share that exists.</p>
            <Loading loaded={status}>{() => <Loading loaded={copies}>{() => <Loading loaded={shares}>{() => {
                if (!status.data!.job) {
                    return <p class="muted">No backup job, so there is nothing to restore from yet.</p>;
                }
                if (!copies.data!.length) {
                    return <p class="muted">The backup pool holds no shares yet. They arrive after the first backup.</p>;
                }
                const existing = shares.data!.map(share => share.name.toLowerCase());
                return (
                    <div class="stack">
                        {copies.data!.map(copy => {
                            const exists = existing.includes(copy.name.toLowerCase());
                            return (
                                <div class="row" key={copy.dataset}>
                                    <strong>{copy.name}</strong>
                                    <span class="small muted">from {copy.pool}, {plural(copy.snapshots, "snapshot")}</span>
                                    <Button busy={action.busy} disabled={exists}
                                            onClick={() => action.run(() => fnas(["backup", "restore", copy.name]))}>Restore</Button>
                                    {exists && <span class="small muted">A share called {copy.name} exists</span>}
                                </div>
                            );
                        })}
                        <Result outcome={action.outcome} />
                    </div>
                );
            }}</Loading>}</Loading>}</Loading>
        </Card>
    );
}

export function Backups() {
    const [round, setRound] = useState(0);
    return (
        <Page intro="Earlier versions of files, a nightly copy on another pool, and bringing shares back from it.">
            <SnapshotSchedules />
            <BackupJob onChange={() => setRound(round + 1)} />
            <RestoreShares key={round} />
        </Page>
    );
}
