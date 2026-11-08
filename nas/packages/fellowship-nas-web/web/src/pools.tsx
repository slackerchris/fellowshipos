// Pool creation and import, shared by the Storage page and the setup wizard
import { useEffect, useState } from "preact/hooks";
import {
    Disk, ImportablePool, LAYOUT_NAMES, LayoutOption, SavedConfig, diskName, fnas, fnasJson, humanSize, when,
} from "./api";
import { Badge, Button, Card, Confirm, Field, Loading, Notice, Result, useAction, useLoad } from "./ui";

const POOL_NAME = /^[a-z][a-z0-9_-]{0,31}$/;

export function erasePhrase(count: number) {
    return `erase ${count} disk${count === 1 ? "" : "s"}`;
}

/** "RAIDZ2, 2 groups of 8 disks, 1 spare" */
export function describeLayout(option: LayoutOption) {
    const name = LAYOUT_NAMES[option.layout];
    const groups = option.layout === "single" ? name
        : option.groups === 1 ? `${name} of ${option.width} disks`
        : `${name}, ${option.groups} groups of ${option.width} disks`;
    return groups + (option.spares ? `, ${option.spares} spare${option.spares === 1 ? "" : "s"}` : "");
}

export function survival(option: LayoutOption) {
    if (!option.survives) {
        return "No redundancy: losing the disk loses the pool";
    }
    const disks = `${option.survives} failed disk${option.survives === 1 ? "" : "s"}`;
    return option.groups === 1 ? `Survives ${disks}` : `Each group survives ${disks}`;
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

export function CreatePool({ onCreated, taken = [] }: { onCreated: (name: string) => void; taken?: string[] }) {
    const disks = useLoad(() => fnasJson<Disk[]>(["disk", "list"]));
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
    const key = selected.map(disk => disk.path).join(" ");
    useEffect(() => {
        setOptions(null);
        setPicked(0);
        if (!selected.length) {
            return;
        }
        let current = true;
        fnasJson<LayoutOption[]>(["pool", "layouts", ...selected.map(disk => disk.by_id || disk.path)])
            .then(found => current && setOptions(found), () => current && setOptions([]));
        return () => { current = false; };
    }, [key]);

    const option = options?.[picked];
    const needsWipe = selected.some(disk => disk.state === "has-data");
    const problem = !POOL_NAME.test(name) ? "Use lowercase letters, numbers, - and _, starting with a letter."
        : taken.includes(name) ? `There is already a pool called ${name}.`
        : !selected.length ? "Choose the disks for the pool."
        : !option ? "Working out the layouts…"
        : needsWipe && !wipe ? "Some chosen disks hold data: tick the box to erase them."
        : "";

    return (
        <Card title="Create a pool">
            <Loading loaded={disks}>{() => {
                if (!usable.length) {
                    return <Notice>No free disks. Disks already in a pool or holding the system aren't offered.</Notice>;
                }
                const shown = all ? options || [] : (options || []).slice(0, SHOWN_LAYOUTS);
                return (
                    <form class="stack" onSubmit={event => { event.preventDefault(); setConfirming(true); }}>
                        <Field label="Pool name" hint="Shares live under /NAME/shares">
                            <input value={name} onInput={event => setName(event.currentTarget.value)} />
                        </Field>
                        <div class="row">
                            <span class="field-label">Disks</span>
                            {usable.length > 1 && (
                                <Button onClick={() => setChosen(chosen.length === usable.length ? [] : usable.map(disk => disk.path))}>
                                    {chosen.length === usable.length ? "Clear" : `Choose all ${usable.length}`}</Button>
                            )}
                        </div>
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
                                            <span class="small">{humanSize(choice.usable)} usable · {survival(choice)}</span>
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
                        {problem && selected.length > 0 && <p class="muted small">{problem}</p>}
                        <div class="row">
                            <Button kind="primary" type="submit" disabled={!!problem}>Create pool</Button>
                        </div>
                        {confirming && option && (
                            <Confirm title="Create pool" phrase={erasePhrase(selected.length)}
                                     busy={action.busy} outcome={action.outcome}
                                     onCancel={() => { setConfirming(false); action.clear(); }}
                                     onConfirm={typed => action.run(async () => {
                                         await fnas(["pool", "create", name, option.layout,
                                                     ...selected.map(disk => disk.by_id || disk.path),
                                                     "--width", String(option.width), "--spares", String(option.spares),
                                                     "--confirm", typed, ...(wipe ? ["--wipe"] : [])]);
                                         setConfirming(false);
                                         onCreated(name);
                                     })}>
                                <p>{describeLayout(option)}. Everything on these disks will be erased:</p>
                                <ul>{selected.map(disk => <li key={disk.path} class="mono small">{diskName(disk)}</li>)}</ul>
                            </Confirm>
                        )}
                    </form>
                );
            }}</Loading>
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
