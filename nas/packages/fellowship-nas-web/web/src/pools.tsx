// Pool creation and import, shared by the Storage page and the setup wizard
import { useEffect, useState } from "preact/hooks";
import {
    Disk, Engine, ImportablePool, LAYOUT_NAMES, LayoutOption, SavedConfig, diskName, fnas, fnasJson, humanSize, plural, when,
} from "./api";
import { Badge, Button, Card, Confirm, Field, Loading, Notice, Result, useAction, useLoad } from "./ui";

const POOL_NAME = /^[a-z][a-z0-9_-]{0,31}$/;

export function erasePhrase(count: number) {
    return `erase ${count} disk${count === 1 ? "" : "s"}`;
}

const PARITY_NAMES = ["", "Striped with parity", "Striped with double parity", "Striped with triple parity"];

/** What a layout gives, in plain words: "Striped with double parity, in 2 groups of 8 disks" */
export function describeLayout(option: LayoutOption) {
    const text = option.layout === "single" ? "One disk, no protection"
        : option.layout === "raid10" ? `Two copies of everything, striped across ${option.width} disks`
        : option.layout === "raid5" || option.layout === "raid6"
            ? `${PARITY_NAMES[option.layout === "raid5" ? 1 : 2]} across ${option.width} disks`
        : option.layout === "mirror"
            ? option.groups === 1 ? "Two copies of everything" : `Two copies of everything, on ${option.groups} pairs of disks`
        : PARITY_NAMES[option.survives] + (option.groups === 1 ? ` across ${option.width} disks`
                                           : `, in ${option.groups} groups of ${option.width} disks`);
    return text + (option.spares ? `, plus ${plural(option.spares, "spare disk")}` : "");
}

/** The name ZFS gives it, for the small print: "2 × RAIDZ2 of 8 disks, 1 hot spare" */
export function technicalLayout(option: LayoutOption) {
    const name = LAYOUT_NAMES[option.layout];
    const groups = option.layout === "single" ? name
        : option.layout === "raid10" ? `${name} · Two copies, ${option.width} disks`
        : `${option.groups > 1 ? `${option.groups} × ` : ""}${name} of ${option.width} disks`;
    return groups + (option.spares ? `, ${plural(option.spares, "hot spare")}` : "");
}

export function survival(option: LayoutOption) {
    if (!option.survives) {
        return "Losing the disk loses the pool";
    }
    const disks = option.survives === 1 ? "1 disk" : `${option.survives} disks`;
    const where = option.layout === "raid10" ? "Any 1 disk can fail, and more if they hold different copies"
        : option.groups === 1 ? `Any ${disks} can fail`
        : `${disks[0].toUpperCase()}${disks.slice(1)} in each ${option.layout === "mirror" ? "pair" : "group"} can fail`;
    return where + (option.spares ? ", and a spare takes over by itself" : "");
}

const SHOWN_LAYOUTS = 4;

/** The disks a pool, group or spare can use, to tick. */
export function DiskPicker({ disks, chosen, setChosen, single }: {
    disks: Disk[];
    chosen: string[];
    setChosen: (paths: string[]) => void;
    single?: boolean;
}) {
    return (
        <div class="field">
            {disks.map(disk => (
                <label class="choice" key={disk.path}>
                    <input type={single ? "radio" : "checkbox"} name="disk" checked={chosen.includes(disk.path)}
                           onChange={event => setChosen(single ? [disk.path] : event.currentTarget.checked
                               ? [...chosen, disk.path] : chosen.filter(path => path !== disk.path))} />
                    <span>
                        <strong>{humanSize(disk.size)}</strong> {disk.kind === "partition" ? "Free part of the system disk" : disk.model || "Disk"}{" "}
                        <span class="muted small mono">{disk.bay ? `${disk.bay} · ` : ""}{diskName(disk)}</span>
                        {disk.state === "has-data" && <><br /><span class="small">⚠ {disk.reason}</span></>}
                    </span>
                </label>
            ))}
        </div>
    );
}

export const usableDisks = (disks: Disk[]) => disks.filter(disk => disk.state === "empty" || disk.state === "has-data");

/** What each engine is for, as the first step of creating a pool shows it. */
const ENGINE_INFO: Record<Engine["name"], string> = {
    zfs: "Best for important files. Checks and repairs every block as it reads it, and takes snapshots. "
        + "Disks should be the same size.",
    mergerfs: "Best for large media libraries on disks of any size. Each disk holds whole files and stays "
        + "readable on its own; parity disks are updated every night.",
    mdadm: "Classic RAID 1, 5, 6 or 10, then formatted with ext4 or XFS. Fast, and any Linux machine can read it; "
        + "no snapshots or self-repair.",
    btrfs: "Mirrors with checksums and snapshots, on disks of any size.",
};

const COMPARISON: [string, string, string, string, string][] = [
    ["", "ZFS", "Unraid-style", "Traditional RAID", "Btrfs"],
    ["Protection", "Mirrors, RAIDZ1/2/3, spares", "1–6 parity disks", "RAID 1, 5, 6, 10, spares", "Two or three copies"],
    ["Format", "ZFS itself", "XFS on each disk", "ext4 or XFS, your choice", "Btrfs itself"],
    ["Mixed disk sizes", "Wastes space", "Works", "Wastes space", "Works"],
    ["Add one disk later", "Yes", "Yes", "Yes", "Yes"],
    ["Finds and repairs damaged files", "Yes, live", "At the nightly sync", "No", "Yes, live"],
    ["New files protected", "At once", "After the nightly sync", "At once", "At once"],
    ["Snapshots and Previous Versions", "Yes", "No", "No", "Yes"],
    ["Too many disks fail", "Pool lost", "Only those disks' files lost", "Array lost", "Pool lost"],
    ["Idle disks spin down", "No", "Yes", "No", "No"],
];

function EnginePicker({ engines, chosen, setChosen }: {
    engines: Engine[];
    chosen: string;
    setChosen: (name: string) => void;
}) {
    const [comparing, setComparing] = useState(false);
    return (
        <div class="field">
            <div class="row">
                <span class="field-label">Kind of storage</span>
                <Button onClick={() => setComparing(!comparing)}>{comparing ? "Hide comparison" : "Compare them"}</Button>
            </div>
            {engines.map(engine => (
                <label class={`choice ${engine.available ? "" : "disabled"}`} key={engine.name}>
                    <input type="radio" name="engine" checked={chosen === engine.name} disabled={!engine.available}
                           onChange={() => setChosen(engine.name)} />
                    <span>
                        <strong>{engine.title}</strong>{" "}
                        {!engine.available && <Badge kind="neutral">{engine.reason}</Badge>}<br />
                        <span class="small">{ENGINE_INFO[engine.name]}</span>
                    </span>
                </label>
            ))}
            {comparing && (
                <div class="table-wrap">
                    <table class="small">
                        <thead><tr>{COMPARISON[0].map(cell => <th key={cell}>{cell}</th>)}</tr></thead>
                        <tbody>
                            {COMPARISON.slice(1).map(row => (
                                <tr key={row[0]}>{row.map((cell, index) => index ? <td key={index}>{cell}</td>
                                                                              : <th key={index} scope="row">{cell}</th>)}</tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}

export function CreatePool({ onCreated, taken = [] }: { onCreated: (name: string) => void; taken?: string[] }) {
    const disks = useLoad(() => fnasJson<Disk[]>(["disk", "list"]));
    const engines = useLoad(() => fnasJson<Engine[]>(["pool", "engines"]));
    const [engine, setEngine] = useState("");
    const [filesystem, setFilesystem] = useState<"xfs" | "ext4">("xfs");
    const [name, setName] = useState(["tank", "data", "media", "backup"].find(free => !taken.includes(free)) || "");
    const [chosen, setChosen] = useState<string[]>([]);
    const [options, setOptions] = useState<LayoutOption[] | null>(null);
    const [picked, setPicked] = useState(0);
    const [all, setAll] = useState(false);
    const [wipe, setWipe] = useState(false);
    const [confirming, setConfirming] = useState(false);
    const action = useAction();

    const usable = usableDisks(disks.data || []);
    const selected = usable.filter(disk => chosen.includes(disk.path));
    // Each engine has its own layouts, so ask the chosen one
    const chosenEngine = engine || engines.data?.find(found => found.available)?.name || "";
    const key = selected.map(disk => disk.path).join(" ");
    useEffect(() => {
        setOptions(null);
        setPicked(0);
        if (!selected.length || !chosenEngine) {
            return;
        }
        let current = true;
        fnasJson<LayoutOption[]>(["pool", "layouts", "--engine", chosenEngine,
                                   ...selected.map(disk => disk.by_id || disk.path)])
            .then(found => current && setOptions(found), () => current && setOptions([]));
        return () => { current = false; };
    }, [key, chosenEngine]);

    const option = options?.[picked];
    const needsWipe = selected.some(disk => disk.state === "has-data");
    const problem = !chosenEngine ? "None of the kinds of storage can be used on this machine yet."
        : !POOL_NAME.test(name) ? "Use lowercase letters, numbers, - and _, starting with a letter."
        : taken.includes(name) ? `There is already a pool called ${name}.`
        : !selected.length ? "Choose the disks for the pool."
        : !option ? "Working out the layouts…"
        : needsWipe && !wipe ? "Some chosen disks hold data: tick the box to erase them."
        : "";

    return (
        <Card title="Create a pool">
            <Loading loaded={disks}>{() => <Loading loaded={engines}>{() => {
                const shown = all ? options || [] : (options || []).slice(0, SHOWN_LAYOUTS);
                return (
                    <form class="stack" onSubmit={event => { event.preventDefault(); setConfirming(true); }}>
                        <EnginePicker engines={engines.data!} chosen={chosenEngine} setChosen={setEngine} />
                        <Field label="Pool name" hint="It is mounted at /mnt/NAME, with shares in /mnt/NAME/shares">
                            <input value={name} onInput={event => setName(event.currentTarget.value)} />
                        </Field>
                        {!usable.length && (
                            <Notice>No free disks. Disks already in a pool or holding the system can't be used; Storage
                                lists every disk and what it's used for.</Notice>
                        )}
                        {usable.length > 0 && <div class="row">
                            <span class="field-label">Disks</span>
                            {usable.length > 1 && (
                                <Button onClick={() => setChosen(chosen.length === usable.length ? [] : usable.map(disk => disk.path))}>
                                    {chosen.length === usable.length ? "Clear" : `Choose all ${usable.length}`}</Button>
                            )}
                        </div>}
                        <DiskPicker disks={usable} chosen={chosen} setChosen={setChosen} />
                        {selected.length > 0 && (
                            <div class="field">
                                <span class="field-label">Layout for {selected.length} disk{selected.length === 1 ? "" : "s"}</span>
                                {options === null && <p class="muted small">Working out the layouts…</p>}
                                {options?.length === 0 && <p class="small">These disks don't make a pool.</p>}
                                {shown.map((choice, index) => (
                                    <label class="choice" key={index}>
                                        <input type="radio" name="layout" checked={picked === index} onChange={() => setPicked(index)} />
                                        <span>
                                            <strong>{describeLayout(choice)}</strong>{" "}
                                            {choice.recommended && <Badge kind="good">Recommended</Badge>}<br />
                                            <span class="small">{humanSize(choice.usable)} usable · {survival(choice)}</span><br />
                                            <span class="small muted">{technicalLayout(choice)}</span>
                                            {choice.unused > 0 && <><br /><span class="small muted">
                                                The disks differ in size, so {humanSize(choice.unused)} goes unused.</span></>}
                                        </span>
                                    </label>
                                ))}
                                {(options?.length || 0) > SHOWN_LAYOUTS && (
                                    <div class="row"><Button onClick={() => setAll(!all)}>
                                        {all ? "Fewer layouts" : `Show all ${options!.length} layouts`}</Button></div>
                                )}
                            </div>
                        )}
                        {needsWipe && (
                            <label class="check">
                                <input type="checkbox" checked={wipe} onChange={event => setWipe(event.currentTarget.checked)} />
                                <span>Erase the data on the disks marked ⚠. Check they aren't from another pool you want first.</span>
                            </label>
                        )}
                        {chosenEngine === "mdadm" && (
                            <div class="field">
                                <span class="field-label">Format</span>
                                <label class="choice">
                                    <input type="radio" name="filesystem" checked={filesystem === "xfs"} onChange={() => setFilesystem("xfs")} />
                                    <span><strong>XFS</strong> <Badge kind="good">Recommended</Badge><br />
                                        <span class="muted small">Built for large files and large arrays, and grows quickly when a
                                            disk is added. It can't be made smaller later.</span></span>
                                </label>
                                <label class="choice">
                                    <input type="radio" name="filesystem" checked={filesystem === "ext4"} onChange={() => setFilesystem("ext4")} />
                                    <span><strong>ext4</strong><br />
                                        <span class="muted small">The most common Linux format, readable almost anywhere. Slower to
                                            check after a crash on a big array.</span></span>
                                </label>
                            </div>
                        )}
                        {problem && (selected.length > 0 || !chosenEngine) && <p class="muted small">{problem}</p>}
                        <div class="row">
                            <Button kind="primary" type="submit" disabled={!!problem}>Create pool</Button>
                        </div>
                        {confirming && option && (
                            <Confirm title="Create pool" phrase={erasePhrase(selected.length)}
                                     busy={action.busy} outcome={action.outcome}
                                     onCancel={() => { setConfirming(false); action.clear(); }}
                                     onConfirm={typed => action.run(async () => {
                                         await fnas(["pool", "create", name, option.layout, "--engine", chosenEngine,
                                                     ...selected.map(disk => disk.by_id || disk.path),
                                                     "--width", String(option.width), "--spares", String(option.spares),
                                                     ...(chosenEngine === "mdadm" ? ["--filesystem", filesystem] : []),
                                                     "--confirm", typed, ...(wipe ? ["--wipe"] : [])]);
                                         setConfirming(false);
                                         onCreated(name);
                                     })}>
                                <p>{describeLayout(option)} ({technicalLayout(option)}
                                    {chosenEngine === "mdadm" ? `, formatted ${filesystem === "xfs" ? "XFS" : "ext4"}` : ""}).
                                    Everything on these disks will be erased:</p>
                                <ul>{selected.map(disk => <li key={disk.path} class="mono small">{diskName(disk)}</li>)}</ul>
                            </Confirm>
                        )}
                    </form>
                );
            }}</Loading>}</Loading>
        </Card>
    );
}

const FOREIGN = "last accessed by another system";

/** Pools on attached disks that aren't imported, with a restore offer for saved settings. */
export function ImportPools({ onChange }: { onChange: () => void }) {
    const scan = useLoad(() => fnasJson<ImportablePool[]>(["pool", "scan"]));
    const [offSwitch, setOffSwitch] = useState<Record<string, boolean>>({});
    const [saved, setSaved] = useState<Record<string, SavedConfig>>({});
    const action = useAction(() => { scan.reload(); onChange(); });

    const importPool = (pool: ImportablePool, readonly: boolean) => action.run(async () => {
        const foreign = (pool.status || "").includes(FOREIGN);
        const output = await fnas(["pool", "import", pool.pool, ...(readonly ? ["--readonly"] : []),
                                   ...(foreign ? ["--force"] : [])]);
        if (!readonly) {
            try {
                const config = await fnasJson<SavedConfig>(["config", "show", pool.pool]);
                if (!config.from_this_install) {
                    setSaved(previous => ({ ...previous, [pool.pool]: config }));
                }
            } catch {
                // No saved settings on this pool
            }
        }
        return output.split("\n")[0];
    });

    const restore = useAction(onChange);
    const pending = Object.entries(saved);

    return (
        <Card title="Pools on attached disks" actions={<Button onClick={scan.reload}>Scan again</Button>}>
            <Loading loaded={scan}>{() => (
                <div class="stack">
                    {!scan.data!.length && <p class="muted">No other pools found on the attached disks.</p>}
                    {scan.data!.map(pool => {
                        const foreign = (pool.status || "").includes(FOREIGN);
                        const usable = pool.state === "ONLINE" || pool.state === "DEGRADED";
                        return (
                            <div class="stack" key={pool.id}>
                                <div class="row">
                                    <strong>{pool.pool}</strong>
                                    <span class="muted small">{pool.state}</span>
                                </div>
                                {pool.status && <p class="small muted">{pool.status}</p>}
                                {foreign && (
                                    <label class="check">
                                        <input type="checkbox" checked={!!offSwitch[pool.id]}
                                               onChange={event => setOffSwitch({ ...offSwitch, [pool.id]: event.currentTarget.checked })} />
                                        <span>The system that used this pool before is switched off. This is normal after a reinstall.</span>
                                    </label>
                                )}
                                <div class="row">
                                    <Button kind="primary" busy={action.busy}
                                            disabled={!usable || (foreign && !offSwitch[pool.id])}
                                            onClick={() => importPool(pool, false)}>Import</Button>
                                    <Button busy={action.busy} disabled={!usable || (foreign && !offSwitch[pool.id])}
                                            onClick={() => importPool(pool, true)}>Import read-only</Button>
                                </div>
                            </div>
                        );
                    })}
                    <Result outcome={action.outcome} />
                    {pending.map(([pool, config]) => (
                        <Notice key={pool}>
                            <div class="stack">
                                <span><strong>{pool}</strong> has settings saved {when(config.saved)}: shares{" "}
                                    {config.shares.map(share => share.name).join(", ") || "none"}, users{" "}
                                    {config.users.map(user => user.name).join(", ") || "none"}
                                    {config.backup ? `, backup to ${config.backup.target}` : ""}.</span>
                                <div class="row">
                                    <Button kind="primary" busy={restore.busy}
                                            onClick={() => restore.run(async () => {
                                                const output = await fnas(["config", "restore", pool]);
                                                setSaved(({ [pool]: _, ...rest }) => rest);
                                                return output;
                                            })}>Restore these settings</Button>
                                    <Button onClick={() => setSaved(({ [pool]: _, ...rest }) => rest)}>Not now</Button>
                                </div>
                            </div>
                        </Notice>
                    ))}
                    <Result outcome={restore.outcome} />
                </div>
            )}</Loading>
        </Card>
    );
}
