// Pool creation and import, shared by the Storage page and the setup wizard
import { useState } from "preact/hooks";
import {
    Disk, ImportablePool, LAYOUTS, Layout, SavedConfig, diskName, fnas, fnasJson, humanSize, when,
} from "./api";
import { Button, Card, Confirm, Field, Loading, Notice, Result, useAction, useLoad } from "./ui";

const POOL_NAME = /^[a-z][a-z0-9_-]{0,31}$/;

function erasePhrase(count: number) {
    return `erase ${count} disk${count === 1 ? "" : "s"}`;
}

export function CreatePool({ onCreated, taken = [] }: { onCreated: (name: string) => void; taken?: string[] }) {
    const disks = useLoad(() => fnasJson<Disk[]>(["disk", "list"]));
    const [name, setName] = useState(taken.includes("tank") ? "" : "tank");
    const [layout, setLayout] = useState<Layout>("mirror");
    const [chosen, setChosen] = useState<string[]>([]);
    const [wipe, setWipe] = useState(false);
    const [confirming, setConfirming] = useState(false);
    const action = useAction();

    return (
        <Card title="Create a pool">
            <Loading loaded={disks}>{() => {
                const usable = disks.data!.filter(disk => disk.state === "empty" || disk.state === "has-data");
                const picked = usable.filter(disk => chosen.includes(disk.path));
                const needsWipe = picked.some(disk => disk.state === "has-data");
                const minimum = LAYOUTS[layout].disks;
                const problem = !POOL_NAME.test(name) ? "Use lowercase letters, numbers, - and _, starting with a letter."
                    : taken.includes(name) ? `There is already a pool called ${name}.`
                    : picked.length < minimum ? `${layout} needs at least ${minimum} disk${minimum === 1 ? "" : "s"}.`
                    : layout === "single" && picked.length !== 1 ? "single takes exactly one disk."
                    : layout === "mirror" && picked.length % 2 ? "A mirror takes an even number of disks."
                    : needsWipe && !wipe ? "Some chosen disks hold data: tick the box to erase them."
                    : "";
                if (!usable.length) {
                    return <Notice>No free disks. Disks already in a pool or holding the system aren't offered.</Notice>;
                }
                return (
                    <form class="stack" onSubmit={event => { event.preventDefault(); setConfirming(true); }}>
                        <Field label="Pool name" hint="Shares live under /NAME/shares">
                            <input value={name} onInput={event => setName(event.currentTarget.value)} />
                        </Field>
                        <div class="field">
                            <span class="field-label">Layout</span>
                            <div class="grid-2">
                                {(Object.keys(LAYOUTS) as Layout[]).map(key => (
                                    <label class="choice" key={key}>
                                        <input type="radio" name="layout" checked={layout === key}
                                               onChange={() => setLayout(key)} />
                                        <span><strong>{key}</strong><br /><span class="muted small">{LAYOUTS[key].text}</span></span>
                                    </label>
                                ))}
                            </div>
                        </div>
                        <div class="field">
                            <span class="field-label">Disks</span>
                            {disks.data!.filter(disk => disk.state !== "os").map(disk => {
                                const free = usable.includes(disk);
                                return (
                                    <label class={`choice ${free ? "" : "disabled"}`} key={disk.path}>
                                        <input type="checkbox" disabled={!free} checked={chosen.includes(disk.path)}
                                               onChange={event => setChosen(event.currentTarget.checked
                                                   ? [...chosen, disk.path] : chosen.filter(path => path !== disk.path))} />
                                        <span>
                                            <strong>{humanSize(disk.size)}</strong> {disk.model || "Disk"}{" "}
                                            <span class="muted small mono">{diskName(disk)}</span>
                                            {disk.reason && <><br /><span class={disk.state === "has-data" ? "small" : "muted small"}>
                                                {disk.state === "has-data" ? "⚠ " : ""}{disk.reason}</span></>}
                                        </span>
                                    </label>
                                );
                            })}
                        </div>
                        {needsWipe && (
                            <label class="check">
                                <input type="checkbox" checked={wipe} onChange={event => setWipe(event.currentTarget.checked)} />
                                <span>Erase the data on the disks marked ⚠. Check they aren't from another pool you want first.</span>
                            </label>
                        )}
                        {problem && picked.length > 0 && <p class="muted small">{problem}</p>}
                        <div class="row">
                            <Button kind="primary" type="submit" disabled={!!problem}>Create pool</Button>
                        </div>
                        {confirming && (
                            <Confirm title="Create pool" phrase={erasePhrase(picked.length)}
                                     busy={action.busy} outcome={action.outcome}
                                     onCancel={() => { setConfirming(false); action.clear(); }}
                                     onConfirm={typed => action.run(async () => {
                                         await fnas(["pool", "create", name, layout,
                                                     ...picked.map(disk => disk.by_id || disk.path),
                                                     "--confirm", typed, ...(wipe ? ["--wipe"] : [])]);
                                         setConfirming(false);
                                         onCreated(name);
                                     })}>
                                <p>Everything on these disks will be erased:</p>
                                <ul>{picked.map(disk => <li key={disk.path} class="mono small">{diskName(disk)}</li>)}</ul>
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
