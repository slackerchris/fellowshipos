import { Fragment } from "preact";
import { useState } from "preact/hooks";
import {
    Access, FnasError, Group, NfsClient, NfsOptions, Protocol, Share, SharingSettings, SmbOptions, User, accessArgs, fnas,
    fnasJson, onOff,
} from "./api";
import { PolicyEditor } from "./backups";
import { AccessEditor, CreateShare, LanSubnet, NfsServer, SmbServer, WaysToShare, nobody } from "./sharing";
import { Badge, Button, Card, Confirm, Field, Loading, Notice, Page, Result, useAction, useLoad } from "./ui";

/** "kim (read), group family (read and write)" */
export function describeAccess(share: Share) {
    if (share.mode === "everyone") {
        return "everyone";
    }
    const level = (found: string) => found === "write" ? "read and write" : "read";
    const listed = [...Object.entries(share.access.users).map(([name, found]) => `${name} (${level(found)})`),
                    ...Object.entries(share.access.groups).map(([name, found]) => `group ${name} (${level(found)})`)];
    return listed.join(", ") || "nobody yet";
}

function AccessTab({ share, onDone }: { share: Share; onDone: () => void }) {
    const users = useLoad(() => fnasJson<User[]>(["user", "list"]));
    const groups = useLoad(() => fnasJson<Group[]>(["group", "list"]));
    const [everyone, setEveryone] = useState(share.mode === "everyone");
    const [access, setAccess] = useState<Access>(share.access);
    const action = useAction(onDone);
    return (
        <Loading loaded={users}>{() => <Loading loaded={groups}>{() => (
            <form class="stack" onSubmit={event => {
                event.preventDefault();
                action.run(() => fnas(["share", "access", share.name, ...accessArgs(everyone, access)]));
            }}>
                <AccessEditor everyone={everyone} setEveryone={setEveryone} access={access} setAccess={setAccess}
                              users={users.data!} groups={groups.data!} />
                <p class="muted small">Saving rewrites the permissions of every file in {share.name}, which takes a while on a
                    large share. Turning guests off happens too when it's no longer for everyone.</p>
                <div class="row"><Button kind="primary" type="submit" busy={action.busy}
                                         disabled={!everyone && nobody(access)}>Save</Button></div>
                <Result outcome={action.outcome} />
            </form>
        )}</Loading>}</Loading>
    );
}

function SmbTab({ share, onDone }: { share: Share; onDone: () => void }) {
    const [options, setOptions] = useState<SmbOptions>(share.smb_options);
    const action = useAction(onDone);
    const set = <K extends keyof SmbOptions>(key: K, value: SmbOptions[K]) => setOptions({ ...options, [key]: value });
    const check = (key: "readonly" | "hidden" | "guest" | "recycle" | "timemachine" | "previous_versions", label: string,
                   disabled = "") => (
        <label class={`check ${disabled ? "disabled" : ""}`}>
            <input type="checkbox" checked={options[key]} disabled={!!disabled}
                   onChange={event => set(key, event.currentTarget.checked)} />
            <span>{label}{disabled && <span class="muted small"> ({disabled})</span>}</span>
        </label>
    );
    return (
        <form class="stack" onSubmit={event => {
            event.preventDefault();
            action.run(() => fnas(["share", "smb", share.name, "--readonly", onOff(options.readonly),
                                   "--hidden", onOff(options.hidden), "--guest", onOff(options.guest),
                                   "--recycle", onOff(options.recycle), "--recycle-days", String(options.recycle_days),
                                   "--timemachine", onOff(options.timemachine),
                                   "--timemachine-size", String(options.timemachine_size),
                                   "--previous-versions", onOff(options.previous_versions)]));
        }}>
            {!share.smb && <Notice>SMB is off for this share. These settings apply once it's turned on.</Notice>}
            {check("readonly", "Read-only: nobody can change files over SMB")}
            {check("hidden", "Hidden: not listed when browsing the NAS, but still reachable by its name")}
            {check("guest", "Guests: anyone on the home network can read it without a password",
                   share.mode === "everyone" ? "" : "only for shares that are for everyone")}
            {check("recycle", "Recycle bin: files deleted over SMB go to .recycle in the share first")}
            {options.recycle && (
                <Field label="Days the recycle bin keeps files">
                    <input type="number" min={1} max={3650} value={options.recycle_days}
                           onInput={event => set("recycle_days", Number(event.currentTarget.value))} />
                </Field>
            )}
            {check("timemachine", "Time Machine: Macs can back up to this share")}
            {options.timemachine && (
                <Field label="Most Time Machine may use, in GB" hint="0 for no limit; Macs remove old backups to stay under it">
                    <input type="number" min={0} value={options.timemachine_size}
                           onInput={event => set("timemachine_size", Number(event.currentTarget.value))} />
                </Field>
            )}
            {check("previous_versions", "Previous Versions: Windows shows this share's snapshots",
                   share.engine === "zfs" || share.engine === "btrfs" ? "" : "this kind of storage has no snapshots")}
            <div class="row"><Button kind="primary" type="submit" busy={action.busy}>Save</Button></div>
            <Result outcome={action.outcome} />
        </form>
    );
}

const MAPPINGS: Record<NfsOptions["mapping"], string> = {
    auto: "Follow who can use the share (recommended)",
    all: "Everyone as the share account (all_squash)",
    root: "Real users, with root as nobody (root_squash)",
    none: "Real users, including root (no_root_squash): for hypervisors and backup servers you trust",
};

function NfsTab({ share, lan, onDone }: { share: Share; lan: string; onDone: () => void }) {
    const [options, setOptions] = useState<NfsOptions>(share.nfs_options);
    const [host, setHost] = useState("");
    const action = useAction(onDone);
    const clients: NfsClient[] = options.clients;
    const setClients = (found: NfsClient[]) => setOptions({ ...options, clients: found });
    return (
        <form class="stack" onSubmit={event => {
            event.preventDefault();
            action.run(() => fnas(["share", "nfs", share.name,
                                   ...(clients.length ? clients.flatMap(c => ["--client", `${c.host}=${c.access}`])
                                       : ["--home-network"]),
                                   "--readonly", onOff(options.readonly), "--mapping", options.mapping,
                                   "--async", onOff(options.async)]));
        }}>
            {!share.nfs && <Notice>NFS is off for this share. These settings apply once it's turned on.</Notice>}
            <div class="field">
                <span class="field-label">Computers that can mount it</span>
                {!clients.length && <p class="small">The home network, <span class="mono">{lan}</span>, read and write.</p>}
                {clients.map((client, index) => (
                    <div class="pick-row" key={client.host}>
                        <span class="mono">{client.host}</span>
                        <select value={client.access} onChange={event => setClients(clients.map((c, i) => i === index
                            ? { ...c, access: event.currentTarget.value as NfsClient["access"] } : c))}>
                            <option value="rw">Read and write</option>
                            <option value="ro">Read only</option>
                        </select>
                        <Button onClick={() => setClients(clients.filter((_, i) => i !== index))}>Remove</Button>
                    </div>
                ))}
                <div class="pick-row">
                    <input aria-label="Network or address" value={host} placeholder="192.168.1.20 or 192.168.1.0/24"
                           onInput={event => setHost(event.currentTarget.value)} />
                    <Button disabled={!host.trim()} onClick={() => {
                        setClients([...(clients.length ? clients : [{ host: lan, access: "rw" as const }]),
                                    { host: host.trim(), access: "rw" }]);
                        setHost("");
                    }}>Add</Button>
                    {clients.length > 0 && <Button onClick={() => setClients([])}>Back to the home network</Button>}
                </div>
                <span class="field-hint">Private addresses only; a share is never open to everyone on the internet.</span>
            </div>
            <label class="check">
                <input type="checkbox" checked={options.readonly}
                       onChange={event => setOptions({ ...options, readonly: event.currentTarget.checked })} />
                <span>Read-only for every computer</span>
            </label>
            <Field label="User mapping">
                <select value={options.mapping}
                        onChange={event => setOptions({ ...options, mapping: event.currentTarget.value as NfsOptions["mapping"] })}>
                    {(Object.keys(MAPPINGS) as NfsOptions["mapping"][]).map(key => <option key={key} value={key}>{MAPPINGS[key]}</option>)}
                </select>
            </Field>
            {options.mapping === "none" && <Notice kind="warning">Root on any allowed computer can then change or delete
                every file in this share.</Notice>}
            <label class="check">
                <input type="checkbox" checked={options.async}
                       onChange={event => setOptions({ ...options, async: event.currentTarget.checked })} />
                <span>Fast writes (async): quicker, but a power cut can lose the last few seconds of writes</span>
            </label>
            <div class="row"><Button kind="primary" type="submit" busy={action.busy}>Save</Button></div>
            <Result outcome={action.outcome} />
        </form>
    );
}

const TABS = { access: "Who can use it", smb: "SMB", nfs: "NFS", snapshots: "Snapshots" } as const;

function ShareSettings({ share, lan, onDone }: { share: Share; lan: string; onDone: () => void }) {
    const [tab, setTab] = useState<keyof typeof TABS>("access");
    return (
        <div class="stack">
            <div class="segmented" role="tablist">
                {(Object.keys(TABS) as (keyof typeof TABS)[]).map(key => (
                    <button key={key} type="button" role="tab" aria-selected={tab === key} class={tab === key ? "current" : ""}
                            onClick={() => setTab(key)}>{TABS[key]}</button>
                ))}
            </div>
            {tab === "access" && <AccessTab share={share} onDone={onDone} />}
            {tab === "smb" && <SmbTab share={share} onDone={onDone} />}
            {tab === "nfs" && <NfsTab share={share} lan={lan} onDone={onDone} />}
            {tab === "snapshots" && <PolicyEditor share={share} onDone={onDone} />}
        </div>
    );
}

/** fnas states the exact phrase and what will be lost, so ask it rather than guess. */
async function deletionTerms(name: string): Promise<{ phrase: string; lost: string[] }> {
    try {
        await fnas(["share", "delete", name]);
    } catch (error) {
        const text = (error as FnasError).message;
        const phrase = /--confirm "([^"]+)"/.exec(text);
        if (phrase) {
            const lost = text.split("\n").filter(line => line.startsWith("    ")).map(line => line.trim());
            return { phrase: phrase[1], lost };
        }
        throw error;
    }
    throw new FnasError("fnas didn't ask for confirmation");
}

function ShareList({ shares, lan, reload }: { shares: Share[]; lan: string; reload: () => void }) {
    const [editing, setEditing] = useState<string | null>(null);
    const [deleting, setDeleting] = useState<{ name: string; phrase: string; lost: string[] } | null>(null);
    const action = useAction(reload);
    const deleteAction = useAction(reload);
    const host = window.location.hostname;

    if (!shares.length) {
        return <Card title="Shares"><p class="muted">No shares yet.</p></Card>;
    }
    const toggle = (share: Share, protocol: "nfs" | "smb") =>
        action.run(() => fnas(["share", "set", share.name, `--${protocol}`, share[protocol] ? "off" : "on"]));

    return (
        <Card title="Shares">
            <div class="table-wrap">
                <table>
                    <thead><tr><th>Share</th><th>Who can use it</th><th>Shared with</th><th /></tr></thead>
                    <tbody>
                        {shares.map(share => (
                            <Fragment key={share.dataset}>
                                <tr>
                                    <td>
                                        <strong>{share.name}</strong>
                                        {share.readonly && <> <Badge kind="neutral">read-only pool</Badge></>}
                                        <br /><span class="small muted mono">{share.path}</span>
                                        {share.smb && <><br /><span class="small muted mono">{`\\\\${host}\\${share.name}`}</span></>}
                                        {share.nfs && <><br /><span class="small muted mono">{`${host}:${share.path}`}</span></>}
                                    </td>
                                    <td class="small">{describeAccess(share)}</td>
                                    <td>
                                        <div class="stack">
                                            <label class="check">
                                                <input type="checkbox" checked={share.smb} disabled={action.busy || share.readonly}
                                                       onChange={() => toggle(share, "smb")} />
                                                <span>SMB{share.smb && share.smb_options.readonly ? " (read-only)" : ""}
                                                    {share.smb && share.smb_options.guest ? " (guests)" : ""}</span>
                                            </label>
                                            <label class="check">
                                                <input type="checkbox" checked={share.nfs} disabled={action.busy || share.readonly}
                                                       onChange={() => toggle(share, "nfs")} />
                                                <span>NFS{share.nfs && share.nfs_options.clients.length
                                                    ? ` (${share.nfs_options.clients.length} chosen computers)` : ""}</span>
                                            </label>
                                            <span class="small muted">SFTP, FTP, WebDAV: not built yet</span>
                                        </div>
                                    </td>
                                    <td class="actions">
                                        {!share.readonly && (
                                            <div class="row row-end">
                                                <Button onClick={() => setEditing(editing === share.name ? null : share.name)}>
                                                    {editing === share.name ? "Close" : "Settings"}</Button>
                                                <Button busy={deleteAction.busy && !deleting} onClick={() => deleteAction.run(async () => {
                                                    setDeleting({ name: share.name, ...await deletionTerms(share.name) });
                                                })}>Delete</Button>
                                            </div>
                                        )}
                                    </td>
                                </tr>
                                {editing === share.name && (
                                    <tr><td colSpan={4}>
                                        <ShareSettings share={share} lan={lan} onDone={reload} />
                                    </td></tr>
                                )}
                            </Fragment>
                        ))}
                    </tbody>
                </table>
            </div>
            <Result outcome={action.outcome} />
            {!deleting && <Result outcome={deleteAction.outcome} />}
            {deleting && (
                <Confirm title="Delete share" phrase={deleting.phrase} busy={deleteAction.busy} outcome={deleteAction.outcome}
                         onCancel={() => { setDeleting(null); deleteAction.clear(); }}
                         onConfirm={typed => deleteAction.run(async () => {
                             const output = await fnas(["share", "delete", deleting.name, "--confirm", typed]);
                             setDeleting(null);
                             return output;
                         })}>
                    <p>Every file in <strong>{deleting.name}</strong> is destroyed, along with its earlier versions:</p>
                    {deleting.lost.length
                        ? <ul class="small mono">{deleting.lost.map(snapshot => <li key={snapshot}>{snapshot}</li>)}</ul>
                        : <p class="muted small">It has no snapshots.</p>}
                    <p>A backup on another pool, if there is one, is kept.</p>
                </Confirm>
            )}
        </Card>
    );
}

export function Shares() {
    const shares = useLoad(() => fnasJson<Share[]>(["share", "list"]));
    const protocols = useLoad(() => fnasJson<Protocol[]>(["share", "services"]));
    const sharing = useLoad(() => fnasJson<SharingSettings>(["sharing", "show"]));
    const lan = useLoad(async () => (await fnas(["lan", "show"])).trim());
    const reload = () => { shares.reload(); protocols.reload(); };
    return (
        <Page intro="Folders that computers on your home network can open, who can use each one, and how.">
            <Loading loaded={protocols}>{() => <WaysToShare protocols={protocols.data!} shares={shares.data || []} />}</Loading>
            <Loading loaded={shares}>{() => <ShareList shares={shares.data!} lan={lan.data || "the home network"} reload={reload} />}</Loading>
            <CreateShare onCreated={reload} />
            <Notice>People's accounts and passwords are in Users: each person's one password works for SMB.</Notice>
            <Loading loaded={sharing}>{() => <>
                <SmbServer settings={sharing.data!.smb} reload={sharing.reload} />
                <NfsServer settings={sharing.data!.nfs} reload={sharing.reload} />
            </>}</Loading>
            <LanSubnet />
        </Page>
    );
}

