import { Fragment } from "preact";
import { useState } from "preact/hooks";
import {
    Access, FnasError, Group, NfsClient, NfsOptions, Protocol, Share, SharingSettings, SmbOptions, User, accessArgs, fnas,
    fnasJson, onOff,
} from "./api";
import { NO_SNAPSHOTS, PolicyEditor, snapshotsWork } from "./backups";
import { AccessEditor, CreateShare, LanSubnet, NfsServer, SmbServer, nobody } from "./sharing";
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
    const groups = useLoad(() => fnasJson<Group[]>(["group", "list", "--system"]));
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
                   snapshotsWork(share) ? "" : NO_SNAPSHOTS.toLowerCase())}
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
            <div class="segmented subtle share-panel-tabs" role="tablist">
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

/** "Sam, Family", "Everyone": who can use a share, without the read or write detail. */
export function accessNames(share: Share) {
    if (share.mode === "everyone") {
        return "Everyone";
    }
    const names = [...Object.keys(share.access.users), ...Object.keys(share.access.groups)]
        .map(name => name.charAt(0).toUpperCase() + name.slice(1));
    if (!names.length) {
        return "Nobody yet";
    }
    return names.length > 3 ? `${names.slice(0, 3).join(", ")} +${names.length - 3}` : names.join(", ");
}

/** The protocols a share is on, as one short line: "SMB · NFS". */
function protocolsOf(share: Share) {
    return [share.smb ? "SMB" : "", share.nfs ? "NFS" : ""].filter(Boolean).join(" · ");
}

/** Where to reach a share from another machine, per protocol it is on. */
function connectLines(share: Share) {
    const host = window.location.hostname;
    const lines = [];
    if (share.smb) {
        lines.push(`\\\\${host}\\${share.name}`, `smb://${host}/${share.name}`);
    }
    if (share.nfs) {
        lines.push(`mount ${host}:${share.path} /media/${share.name}`);
    }
    return lines;
}

const FolderIcon = () => (
    <svg class="folder-icon" viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
        <path d="M2.5 5.6c0-.7.6-1.3 1.3-1.3h3.4c.4 0 .7.1 1 .4l1.4 1.4h6.6c.7 0 1.3.6 1.3 1.3v7.2c0 .7-.6 1.3-1.3 1.3H3.8c-.7 0-1.3-.6-1.3-1.3z"
              fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" />
    </svg>
);

/** One share's panel: how to reach it, which ways it is shared, and its settings. */
function SharePanel({ share, lan, busy, toggle, onDone }: {
    share: Share; lan: string; busy: boolean; toggle: (share: Share, protocol: "nfs" | "smb") => void;
    onDone: () => void;
}) {
    const lines = connectLines(share);
    return (
        <div class="stack share-panel">
            <div class="connect-block">
                <span class="field-label">Connect from another device</span>
                {lines.length
                    ? lines.map(line => <code key={line} class="mono small">{line}</code>)
                    : <span class="muted small">Turn on SMB or NFS below to reach it from another machine.</span>}
                <span class="muted small mono">{share.path}</span>
            </div>
            <div class="row share-toggles">
                <label class="check">
                    <input type="checkbox" checked={share.smb} disabled={busy || share.readonly}
                           onChange={() => toggle(share, "smb")} />
                    <span>SMB{share.smb && share.smb_options.guest ? " (guests)" : ""}</span>
                </label>
                <label class="check">
                    <input type="checkbox" checked={share.nfs} disabled={busy || share.readonly}
                           onChange={() => toggle(share, "nfs")} />
                    <span>NFS{share.nfs && share.nfs_options.clients.length
                        ? ` (${share.nfs_options.clients.length} chosen computers)` : ""}</span>
                </label>
            </div>
            {share.readonly
                ? <p class="muted small">This share is on a read-only pool, so it can't be changed.</p>
                : <ShareSettings share={share} lan={lan} onDone={onDone} />}
        </div>
    );
}

function ShareList({ shares, lan, reload, creating, setCreating }: {
    shares: Share[]; lan: string; reload: () => void; creating: boolean; setCreating: (open: boolean) => void;
}) {
    const [open, setOpen] = useState<string | null>(null);
    const [menu, setMenu] = useState<string | null>(null);
    const [deleting, setDeleting] = useState<{ name: string; phrase: string; lost: string[] } | null>(null);
    const action = useAction(reload);
    const deleteAction = useAction(reload);
    const toggle = (share: Share, protocol: "nfs" | "smb") =>
        action.run(() => fnas(["share", "set", share.name, `--${protocol}`, share[protocol] ? "off" : "on"]));
    const choose = (name: string) => { setMenu(null); setOpen(open === name ? null : name); };

    return (
        <Card title="Shared folders"
              actions={<Button kind="primary" onClick={() => setCreating(!creating)}>+ New share</Button>}>
            <p class="muted small row-intro">Manage access and connect from another device.</p>
            {creating && <CreateShare onCreated={() => { setCreating(false); reload(); }} />}
            {!shares.length && <p class="muted">No shares yet. Use New share to make the first one.</p>}
            {shares.length > 0 && (
                <div class="table-wrap">
                    <table class="share-table">
                        <thead><tr><th>Folder</th><th>Access</th><th>Protocols</th><th /></tr></thead>
                        <tbody>
                            {shares.map(share => (
                                <Fragment key={share.dataset}>
                                    <tr>
                                        <td>
                                            <span class="share-name"><FolderIcon /><strong>{share.name}</strong></span>
                                            {share.readonly && <> <Badge kind="neutral">read-only pool</Badge></>}
                                        </td>
                                        <td class="small">{accessNames(share)}</td>
                                        <td class="small">
                                            {protocolsOf(share) || <span class="muted">Not shared</span>}
                                        </td>
                                        <td class="row-actions">
                                            <button type="button" class="link-button" aria-expanded={open === share.name}
                                                    onClick={() => choose(share.name)}>Connect</button>
                                            <span class="menu-wrap">
                                                <button type="button" class="kebab" aria-haspopup="menu"
                                                        aria-expanded={menu === share.name} aria-label={`More for ${share.name}`}
                                                        onClick={() => setMenu(menu === share.name ? null : share.name)}>⋯</button>
                                                {menu === share.name && (
                                                    <span class="row-menu" role="menu">
                                                        <button type="button" role="menuitem"
                                                                onClick={() => choose(share.name)}>Settings…</button>
                                                        {!share.readonly && (
                                                            <button type="button" role="menuitem" class="danger-text"
                                                                    onClick={() => { setMenu(null); deleteAction.run(async () => {
                                                                        setDeleting({ name: share.name, ...await deletionTerms(share.name) });
                                                                    }); }}>Delete…</button>
                                                        )}
                                                    </span>
                                                )}
                                            </span>
                                        </td>
                                    </tr>
                                    {open === share.name && (
                                        <tr class="share-open">
                                            <td colSpan={4}>
                                                <SharePanel share={share} lan={lan} busy={action.busy}
                                                            toggle={toggle} onDone={reload} />
                                            </td>
                                        </tr>
                                    )}
                                </Fragment>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
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

/** The short state of every way to share, and the ones still to come. */
function WaysToShareStrip({ protocols }: { protocols: Protocol[] }) {
    const here = protocols.filter(protocol => protocol.state !== "not-built" && protocol.state !== "later");
    const planned = protocols.filter(protocol => protocol.state === "not-built" || protocol.state === "later");
    return (
        <Card title="Ways to share">
            <div class="proto-row">
                {here.map(protocol => (
                    <span class="proto" key={protocol.name}>
                        <strong>{protocol.title}</strong>
                        <span class={`state-dot ${protocol.state}`} />
                        <span class="muted small">{protocol.state === "running" ? "Running" : protocol.detail}</span>
                    </span>
                ))}
                {!here.length && <span class="muted small">Nothing is shared yet.</span>}
            </div>
            {planned.length > 0 && (
                <p class="muted small proto-planned">Planned: {planned.map(protocol => protocol.title).join(" · ")}</p>
            )}
        </Card>
    );
}

export function Shares() {
    const shares = useLoad(() => fnasJson<Share[]>(["share", "list"]));
    const protocols = useLoad(() => fnasJson<Protocol[]>(["share", "services"]));
    const sharing = useLoad(() => fnasJson<SharingSettings>(["sharing", "show"]));
    const lan = useLoad(async () => (await fnas(["lan", "show"])).trim());
    const [tab, setTab] = useState<"folders" | "settings">("folders");
    const [creating, setCreating] = useState(false);
    const reload = () => { shares.reload(); protocols.reload(); };
    return (
        <Page>
            <div class="segmented page-tabs" role="tablist">
                <button type="button" role="tab" aria-selected={tab === "folders"} class={tab === "folders" ? "current" : ""}
                        onClick={() => setTab("folders")}>Shared folders</button>
                <button type="button" role="tab" aria-selected={tab === "settings"} class={tab === "settings" ? "current" : ""}
                        onClick={() => setTab("settings")}>Sharing settings</button>
            </div>
            {tab === "folders" ? <>
                <Loading loaded={shares}>{() => (
                    <ShareList shares={shares.data!} lan={lan.data || "the home network"} reload={reload}
                               creating={creating} setCreating={setCreating} />
                )}</Loading>
                <Loading loaded={protocols}>{() => <WaysToShareStrip protocols={protocols.data!} />}</Loading>
            </> : <>
                <Loading loaded={sharing}>{() => <>
                    <SmbServer settings={sharing.data!.smb} reload={sharing.reload} />
                    <NfsServer settings={sharing.data!.nfs} reload={sharing.reload} />
                </>}</Loading>
                <LanSubnet />
                <Notice>People's accounts and passwords are in Users: each person's one password works for SMB.</Notice>
            </>}
        </Page>
    );
}

