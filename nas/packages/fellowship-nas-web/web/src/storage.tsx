import { useState } from "preact/hooks";
import { PoolDetailsView } from "./pooldetails";
import {
    Disk, Engine, Growth, Job, LAYOUT_NAMES, jobStarted, LayoutOption, Pool, PoolStatus, Vdev, diskName, fill, fnas, fnasJson, humanSize,
    leaves, loadEngines, when,
} from "./api";
import { useJobsFinished } from "./jobs";
import { CreatePool, DiskPicker, ImportPools, erasePhrase, usableDisks } from "./pools";
import { Badge, Button, Card, Confirm, Loading, Notice, Page, Result, Usage, useAction, useLoad } from "./ui";

function healthBadge(health: string) {
    const kind = health === "ONLINE" || health === "AVAIL" ? "good"
        : health === "DEGRADED" || health === "INUSE" ? "warning" : "bad";
    const text = { ONLINE: "Healthy", AVAIL: "Ready", INUSE: "Standing in" }[health] || health.toLowerCase();
    return <Badge kind={kind}>{text}</Badge>;
}

const DISK_STATE = {
    os: "System disk",
    "in-use": "In use",
    "has-data": "Has old data",
    empty: "Empty",
} as const;

/** The disk list's entry for a disk in a pool, which ZFS names by partition. */
function diskFor(vdev: Vdev, disks: Disk[]) {
    return disks.find(disk => disk.by_id === vdev.path)
        || disks.find(disk => disk.by_id && vdev.path.startsWith(disk.by_id + "-part"));
}

function vdevName(vdev: Vdev, disks: Disk[]) {
    const disk = diskFor(vdev, disks);
    if (!vdev.path) {
        // A disk that has gone, named by the disk its engine last saw there when it can be
        return vdev.name.startsWith("/dev/") ? `${vdev.name.split("/").pop()} (missing)` : "Missing disk";
    }
    if (disk?.kind === "partition") {
        return `Free part of the system disk · ${diskName(disk)}`;
    }
    const name = diskName({ by_id: vdev.path, path: vdev.path }).replace(/-part\d+$/, "");
    return disk?.bay ? `${disk.bay} · ${name}` : name;
}

function groupTitle(group: Vdev) {
    if (group.title) {
        return group.title;
    }
    const count = group.disks?.length || 1;
    if (group.type === "single") {
        return "One disk, no redundancy";
    }
    const name = LAYOUT_NAMES[group.type as LayoutOption["layout"]] || group.type;
    return `${name} of ${count} disk${count === 1 ? "" : "s"}`;
}

function ProgressBar({ value, label }: { value: number; label: string }) {
    return (
        <div class="stack">
            <span class="small">{label} · {Math.floor(value * 100)}%</span>
            <div class="usage" role="progressbar" aria-valuenow={Math.floor(value * 100)} aria-valuemin={0} aria-valuemax={100}>
                <div class="usage-fill usage-good" style={{ width: `${value * 100}%` }} />
            </div>
        </div>
    );
}

export function RunningJobs({ jobs }: { jobs: Job[] }) {
    const shown = jobs.filter(job => job.state === "running" || (job.state === "failed" && job.ended
        && Date.now() / 1000 - job.ended < 3600));
    if (!shown.length) {
        return null;
    }
    return (
        <Card title="Working on it">
            <div class="stack">
                {shown.map(job => job.state === "running"
                    ? <ProgressBar key={job.id} value={job.progress} label={job.title} />
                    : <Notice key={job.id} kind="error">{job.title} failed: {job.error}</Notice>)}
                <p class="muted small">These keep going if you close this window or the browser.</p>
            </div>
        </Card>
    );
}

/** Pick a disk to take the place of one that failed, or is failing. */
function Replace({ pool, engine, vdev, disks, done, cancel }: {
    pool: string; engine: Engine; vdev: Vdev; disks: Disk[]; done: (text: string) => void; cancel: () => void;
}) {
    const free = usableDisks(disks).filter(disk => !vdev.size || disk.size >= vdev.size);
    const [chosen, setChosen] = useState<string[]>([]);
    const [wipe, setWipe] = useState(false);
    const action = useAction();
    const disk = free.find(found => found.path === chosen[0]);
    return (
        <Confirm title="Replace disk" phrase={erasePhrase(1)} busy={action.busy} outcome={action.outcome}
                 onCancel={cancel}
                 onConfirm={typed => action.run(async () => {
                     if (!disk) {
                         throw new Error("Choose the new disk first.");
                     }
                     done(jobStarted(await fnas(["pool", "replace", pool, vdev.path || vdev.name, disk.by_id || disk.path,
                                                 "--confirm", typed, ...(wipe ? ["--wipe"] : [])])));
                 })}>
            <p>Replacing <strong>{vdevName(vdev, disks)}</strong> in {pool}. {engine.replace_text}</p>
            {free.length
                ? <DiskPicker disks={free} chosen={chosen} setChosen={setChosen} single />
                : <Notice kind="warning">No free disk is large enough. Connect one{vdev.size ? ` of at least ${humanSize(vdev.size)}` : ""}, then
                    open this again.</Notice>}
            {disk?.state === "has-data" && (
                <label class="check">
                    <input type="checkbox" checked={wipe} onChange={event => setWipe(event.currentTarget.checked)} />
                    <span>Erase the old data on the new disk.</span>
                </label>
            )}
        </Confirm>
    );
}

const SPARES_TEXT = "Add hot spares, which stand in for a failed disk at once";

/** The ways this pool can take more disks: those its engine has, that fit what it's made of. */
function growths(status: PoolStatus, disks: Disk[], engine: Engine): Growth[] {
    // An engine that knows from the pool's disks which ways are open says so; otherwise the shape decides
    const ways = new Set(status.growable || engine.grow || []);
    const found: Growth[] = [];
    const kinds = new Set(status.groups.map(group => `${group.type}:${group.disks?.length || 1}`));
    const [type, width] = [...kinds][0]?.split(":") || [];
    if (ways.has("group") && kinds.size === 1 && type !== "single") {
        found.push({ how: "group", count: Number(width),
                     text: `Add another ${LAYOUT_NAMES[type as LayoutOption["layout"]] || type} group of ${width} disks` });
    }
    for (const group of status.groups) {
        // Parity groups take one more disk where the engine says the pool can; copies don't
        if (ways.has("expand") && status.expandable && group.type !== "single" && group.type !== "mirror") {
            found.push({ how: "expand", target: group.name, count: 1, text: fill(engine.expand_text, { group: group.name }) });
        }
        if (ways.has("mirror") && (group.type === "single" || group.type === "mirror")) {
            const first = leaves(group)[0];
            found.push({ how: "mirror", target: first.path, count: 1,
                         text: group.type === "single"
                             ? `Mirror ${diskFor(first, disks)?.kind === "partition" ? "the free part of the system disk"
                                 : vdevName(first, disks)} onto another disk, so the pool survives one failing`
                             : `Add a third copy to ${group.name}` });
        }
    }
    if (ways.has("spare") && status.groups.some(group => group.type !== "single")) {
        found.push({ how: "spare", count: 0, text: SPARES_TEXT });
    }
    // Ways the engine words itself: any number of disks, or one
    for (const how of ["data", "parity"] as const) {
        if (ways.has(how) && engine.grow_text?.[how]) {
            found.push({ how, count: how === "parity" ? 1 : 0, text: engine.grow_text[how]! });
        }
    }
    return found;
}

function Grow({ pool, engine, status, disks, done, cancel }: {
    pool: string; engine: Engine; status: PoolStatus; disks: Disk[]; done: (text: string) => void; cancel: () => void;
}) {
    const options = growths(status, disks, engine);
    const free = usableDisks(disks);
    const fits = (option: Growth) => free.length >= Math.max(option.count, 1);
    const [picked, setPicked] = useState(Math.max(options.findIndex(fits), 0));
    const [chosen, setChosen] = useState<string[]>([]);
    const [wipe, setWipe] = useState(false);
    const [confirming, setConfirming] = useState(false);
    const action = useAction();
    const option = options[picked];
    const selected = free.filter(disk => chosen.includes(disk.path));
    const problem = !option ? "" : !fits(option) ? "Not enough free disks for this."
        : option.count && selected.length !== option.count
        ? `Choose ${option.count} disk${option.count === 1 ? "" : "s"}.`
        : !selected.length ? "Choose at least one disk."
        : selected.some(disk => disk.state === "has-data") && !wipe ? "Tick the box to erase the disks marked ⚠." : "";
    if (!options.length) {
        return <Notice>This pool can't take more disks.</Notice>;
    }
    return (
        <div class="stack">
            <span class="field-label">How</span>
            {options.map((choice, index) => (
                <label class={`choice ${fits(choice) ? "" : "disabled"}`} key={index}>
                    <input type="radio" name={`grow-${pool}`} checked={picked === index} disabled={!fits(choice)}
                           onChange={() => { setPicked(index); setChosen([]); }} />
                    <span>{choice.text}
                        {!fits(choice) && <><br /><span class="small muted">Needs {choice.count} free disks; there
                            {free.length === 1 ? " is" : " are"} {free.length}.</span></>}</span>
                </label>
            ))}
            <span class="field-label">Disks</span>
            {free.length
                ? <DiskPicker disks={free} chosen={chosen} setChosen={setChosen} single={option.count === 1} />
                : <Notice>No free disks. Connect one, then open this again.</Notice>}
            {selected.some(disk => disk.state === "has-data") && (
                <label class="check">
                    <input type="checkbox" checked={wipe} onChange={event => setWipe(event.currentTarget.checked)} />
                    <span>Erase the data on the disks marked ⚠.</span>
                </label>
            )}
            {problem && <p class="muted small">{problem}</p>}
            <div class="row">
                <Button onClick={cancel}>Cancel</Button>
                <Button kind="primary" disabled={!!problem} onClick={() => setConfirming(true)}>Add disks</Button>
            </div>
            {confirming && (
                <Confirm title="Add disks" phrase={erasePhrase(selected.length)} busy={action.busy} outcome={action.outcome}
                         onCancel={() => { setConfirming(false); action.clear(); }}
                         onConfirm={typed => action.run(async () => {
                             done(jobStarted(await fnas(["pool", "grow", pool, option.how, ...selected.map(disk => disk.by_id || disk.path),
                                              ...(option.target ? ["--target", option.target] : []),
                                              "--confirm", typed, ...(wipe ? ["--wipe"] : [])])));
                         })}>
                    <p>{option.text}. A disk added to {pool} can't be taken out again, except a spare. Everything on these disks
                        will be erased:</p>
                    <ul>{selected.map(disk => <li key={disk.path} class="mono small">{diskName(disk)}</li>)}</ul>
                </Confirm>
            )}
        </div>
    );
}

/** A disk in a group. A failed disk that a spare stands in for, or that is
 * being replaced, is one row saying so. */
function Member({ member, disks, readonly, replace }: {
    member: Vdev; disks: Disk[]; readonly: boolean; replace: (vdev: Vdev) => void;
}) {
    const action = useAction();
    // A spare's entry holds the failed disk (or its replacement under way) and
    // the spare; a replacement's holds the old disk and the new one
    const vdev = leaves(member)[0];
    const last = (found: Vdev) => leaves(found)[leaves(found).length - 1];
    const spare = member.type === "spare" ? last(member) : null;
    const inner = member.type === "replacing" ? member : member.disks?.find(child => child.type === "replacing");
    const replacement = inner ? last(inner) : null;
    const disk = diskFor(vdev, disks);
    const replacing = !!replacement;
    return (
        <tr>
            <td><span class="mono small">{vdevName(vdev, disks)}</span>
                {replacement && <><br /><span class="small">Being replaced by <span class="mono">{vdevName(replacement, disks)}</span></span></>}
                {spare && <><br /><span class="small">Hot spare standing in: <span class="mono">{vdevName(spare, disks)}</span></span></>}
                {vdev.errors > 0 && <><br /><span class="small">{vdev.errors} read, write or checksum errors</span></>}</td>
            <td>{vdev.size ? humanSize(vdev.size) : ""}</td>
            <td>{healthBadge(vdev.state)}</td>
            <td class="actions">
                <div class="row row-end">
                    {disk && <Button busy={action.busy} onClick={() => action.run(() => fnas(["disk", "identify", disk.path]))}>
                        Identify</Button>}
                    {!readonly && !replacing && (
                        <Button kind={vdev.state === "ONLINE" ? "plain" : "primary"} onClick={() => replace(vdev)}>Replace</Button>
                    )}
                </div>
                <Result outcome={action.outcome} />
            </td>
        </tr>
    );
}

function PoolCard({ pool, engine, disks, reload, details }: {
    pool: Pool;
    engine: Engine;
    disks: Disk[];
    reload: () => void;
    details: () => void;
}) {
    const status = useLoad(() => fnasJson<PoolStatus>(["pool", "status", pool.name]));
    const [replacing, setReplacing] = useState<Vdev | null>(null);
    const [growing, setGrowing] = useState(false);
    const [exporting, setExporting] = useState(false);
    const [repairing, setRepairing] = useState(false);
    const action = useAction(() => { status.reload(); reload(); });
    const exportAction = useAction(reload);
    const finished = (text: string) => {
        setReplacing(null);
        setGrowing(false);
        action.run(async () => text);
    };
    useJobsFinished(status.reload);

    return (
        <Card title={pool.name} actions={<>
            <Badge kind="neutral">{engine.title}</Badge>
            {pool.readonly && <Badge kind="neutral">read-only</Badge>}
            {healthBadge(pool.health)}
            <Button onClick={details}>Details and history</Button>
        </>}>
            <div class="stack">
                <div>
                    <Usage used={pool.allocated} total={pool.size} />
                    <span class="small muted">{humanSize(pool.allocated)} used of {humanSize(pool.size)}</span>
                </div>
                <Loading loaded={status}>{() => {
                    const s = status.data!;
                    const lone = s.groups.some(group => group.type === "single");
                    const mismatched = s.groups[0]?.errors || 0;
                    return (
                        <div class="stack">
                            {s.status && <Notice kind="warning">{s.status}</Notice>}
                            {s.last_sync !== undefined && !pool.readonly && (
                                <div class="row">
                                    <span class="small">Parity last synced: {s.last_sync ? when(s.last_sync) : "never"}. Files
                                        changed since then aren't protected until the next sync.</span>
                                    <Button busy={action.busy} disabled={s.scan?.state === "scanning"}
                                            onClick={() => action.run(async () => jobStarted(await fnas(["pool", "sync", pool.name])))}>
                                        Sync now</Button>
                                </div>
                            )}
                            {s.scan?.state === "scanning" && (
                                <ProgressBar value={s.scan.progress}
                                             label={s.scan.function === "resilver" ? "Copying data onto the new disk"
                                                 : s.scan.function === "sync" ? "Bringing parity up to date" : "Checking every block"} />
                            )}
                            {s.expand?.state === "scanning" && <ProgressBar value={s.expand.progress} label="Spreading data onto the added disk" />}
                            {lone && !pool.readonly && (
                                <Notice kind="warning">A disk on its own has no redundancy: if it fails, its data is gone. Add a
                                    second disk as a mirror, and keep a backup.</Notice>
                            )}
                            <div class="table-wrap">
                                <table class="pool-disks">
                                    <thead><tr><th>Disk</th><th>Size</th><th>State</th><th /></tr></thead>
                                    {s.groups.map(group => (
                                        <tbody key={group.name}>
                                            <tr class="group-row"><th colSpan={2}>{groupTitle(group)}</th>
                                                <td>{group.type !== "single" && healthBadge(group.state)}</td><td /></tr>
                                            {(group.disks || []).map(member => (
                                                <Member key={member.name} member={member} disks={disks} readonly={pool.readonly}
                                                        replace={setReplacing} />
                                            ))}
                                        </tbody>
                                    ))}
                                    {s.spares.length > 0 && (
                                        <tbody>
                                            <tr class="group-row"><th colSpan={4}>Hot spares</th></tr>
                                            {s.spares.map(spare => (
                                                <tr key={spare.name}>
                                                    <td class="mono small">{vdevName(spare, disks)}</td>
                                                    <td />
                                                    <td>{healthBadge(spare.state)}</td>
                                                    <td class="actions">
                                                        {spare.state === "AVAIL" && !pool.readonly && (
                                                            <Button busy={action.busy} onClick={() => action.run(
                                                                () => fnas(["pool", "remove-spare", pool.name, spare.path]))}>Remove</Button>
                                                        )}
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    )}
                                </table>
                            </div>
                            {engine.repair && mismatched > 0 && !pool.readonly && (
                                <Notice kind="warning">
                                    <div class="stack">
                                        <span>{fill(engine.repair_notice, { pool: pool.name, count: mismatched })}</span>
                                        <div class="row"><Button onClick={() => setRepairing(true)}>Repair {pool.name}</Button></div>
                                    </div>
                                </Notice>
                            )}
                            {growing && <Grow pool={pool.name} engine={engine} status={s} disks={disks} done={finished}
                                              cancel={() => setGrowing(false)} />}
                            {replacing && <Replace pool={pool.name} engine={engine} vdev={replacing} disks={disks} done={finished}
                                                   cancel={() => setReplacing(null)} />}
                        </div>
                    );
                }}</Loading>
                <Result outcome={action.outcome} />
                <div class="row">
                    {!pool.readonly && !growing && <Button onClick={() => setGrowing(true)}>Add disks</Button>}
                    {!pool.readonly && (
                        <Button busy={action.busy} onClick={() => action.run(async () =>
                            jobStarted(await fnas(["pool", "scrub", pool.name])))}>{engine.check_button}</Button>
                    )}
                    <Button onClick={() => setExporting(true)}>Export</Button>
                </div>
                {!pool.readonly && <p class="muted small">{fill(engine.check_text, { pool: pool.name })}</p>}
                {repairing && (
                    <Confirm title="Repair" phrase={`repair ${pool.name}`} busy={action.busy} outcome={action.outcome}
                             onCancel={() => { setRepairing(false); action.clear(); }}
                             onConfirm={typed => action.run(async () => {
                                 const output = jobStarted(await fnas(["pool", "repair", pool.name, "--confirm", typed]));
                                 setRepairing(false);
                                 return output;
                             })}>
                        <p>{engine.repair_text}</p>
                    </Confirm>
                )}
                <Result outcome={exportAction.outcome} />
            </div>
            {exporting && (
                <Confirm title="Export pool" phrase={pool.name} busy={exportAction.busy} outcome={exportAction.outcome}
                         onCancel={() => { setExporting(false); exportAction.clear(); }}
                         onConfirm={() => exportAction.run(async () => {
                             const output = await fnas(["pool", "export", pool.name]);
                             setExporting(false);
                             return output;
                         })}>
                    <p>Its shares stop and the pool is closed so its disks can be moved. Nothing is erased, and it can be imported again.</p>
                </Confirm>
            )}
        </Card>
    );
}

function Disks({ disks }: { disks: Disk[] }) {
    const action = useAction();
    return (
        <Card title="Disks">
            <div class="table-wrap">
                <table>
                    <thead><tr><th>Disk</th><th>Size</th><th>State</th><th /></tr></thead>
                    <tbody>
                        {disks.map(disk => (
                            <tr key={disk.path}>
                                <td>{disk.kind === "partition" ? "Free part of the system disk" : disk.model || "Disk"}
                                    {disk.bay && <> <Badge kind="neutral">{disk.bay}</Badge></>}
                                    <br /><span class="mono small muted">{diskName(disk)}</span></td>
                                <td>{humanSize(disk.size)}</td>
                                <td>{DISK_STATE[disk.state]}{disk.reason && <><br /><span class="small muted">{disk.reason}</span></>}</td>
                                <td class="actions">
                                    {disk.kind === "disk" && (
                                        <Button busy={action.busy} onClick={() => action.run(() => fnas(["disk", "identify", disk.path]))}>
                                            Identify</Button>
                                    )}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <Result outcome={action.outcome} />
        </Card>
    );
}

export function Storage() {
    const pools = useLoad(() => fnasJson<Pool[]>(["pool", "list"]));
    const disks = useLoad(() => fnasJson<Disk[]>(["disk", "list", "--all"]));
    const engines = useLoad(loadEngines);
    const [creating, setCreating] = useState(false);
    const [created, setCreated] = useState("");
    const [viewing, setViewing] = useState<string | null>(null);
    const reload = () => { pools.reload(); disks.reload(); };
    const jobs = useJobsFinished(reload);

    if (viewing) {
        return <Page><PoolDetailsView name={viewing} back={() => setViewing(null)} /></Page>;
    }
    return (
        <Page intro="Pools hold your shares. Each pool is made from one or more disks.">
            <RunningJobs jobs={jobs} />
            <Loading loaded={pools}>{() => <Loading loaded={disks}>{() => <Loading loaded={engines}>{() => (
                <div class="stack">
                    {!pools.data!.length && (
                        <Card title="Pools"><p class="muted">No pools yet. Create one below, or import one from attached disks.</p></Card>
                    )}
                    {pools.data!.map(pool => <PoolCard key={pool.name} pool={pool} engine={engines.data![pool.engine]}
                                                       disks={disks.data!} reload={reload} details={() => setViewing(pool.name)} />)}
                </div>
            )}</Loading>}</Loading>}</Loading>
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
