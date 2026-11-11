// Share creation, the ways to share, the LAN subnet and SMB passwords, shared by the Shares page and the setup wizard
import { useState } from "preact/hooks";
import {
    Access, Group, Level, Pool, Protocol, Share, SharingSettings, User, accessArgs, fnas, fnasJson, onOff,
} from "./api";
import { Badge, Button, Card, Field, Loading, Notice, Result, useAction, useLoad } from "./ui";

export const STATE_BADGE = {
    running: "good", stopped: "warning", failed: "bad", missing: "bad", "not-built": "neutral", later: "neutral",
    off: "neutral",
} as const;

/** How to reach a share, or every share, over one protocol. */
function connectTo(protocol: Protocol, shares: Share[]) {
    const host = window.location.hostname;
    const example = shares.find(share => share[protocol.name as "smb" | "nfs"]);
    if (protocol.name === "smb") {
        return [`\\\\${host}\\${example?.name || "SHARE"}`, `smb://${host}/${example?.name || "SHARE"}`];
    }
    if (protocol.name === "nfs") {
        return [`mount ${host}:${example?.path || "/mnt/POOL/shares/SHARE"} /media/${example?.name || "SHARE"}`];
    }
    return [];
}

/** Every way to share files, built or not, so nothing is hidden behind having a pool. */
export function WaysToShare({ protocols, shares }: { protocols: Protocol[]; shares: Share[] }) {
    return (
        <Card title="Ways to share">
            <div class="table-wrap">
                <table>
                    <thead><tr><th>Way</th><th>For</th><th>State</th><th>How to connect</th></tr></thead>
                    <tbody>
                        {protocols.map(protocol => (
                            <tr key={protocol.name}>
                                <td><strong>{protocol.title}</strong></td>
                                <td class="small">{protocol.for}</td>
                                <td><Badge kind={STATE_BADGE[protocol.state]}>{protocol.detail}</Badge></td>
                                <td class="small mono">
                                    {connectTo(protocol, shares).map(line => <div key={line}>{line}</div>)}
                                    {protocol.state === "not-built" || protocol.state === "later" ? <span class="muted">–</span> : null}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <p class="muted small">People use their own user name and password, set in Users. Every way of sharing is
                limited to the home network set at the bottom of this page.</p>
        </Card>
    );
}

const SHARE_NAME = /^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$/;

type Choice = "none" | Level;

/** Who can use a share: everyone, or chosen users and groups with read or read and write. */
export function AccessEditor({ everyone, setEveryone, access, setAccess, users, groups }: {
    everyone: boolean;
    setEveryone: (everyone: boolean) => void;
    access: Access;
    setAccess: (access: Access) => void;
    users: User[];
    groups: Group[];
}) {
    const pick = (kind: "users" | "groups", name: string, level: Choice) => {
        const { [name]: _, ...rest } = access[kind];
        setAccess({ ...access, [kind]: level === "none" ? rest : { ...rest, [name]: level } });
    };
    const row = (kind: "users" | "groups", name: string, detail: string) => (
        <div class="pick-row" key={`${kind}-${name}`}>
            <span><strong>{kind === "groups" ? `Group ${name}` : name}</strong>
                {detail && <span class="small muted"> {detail}</span>}</span>
            <select aria-label={`Access for ${name}`} value={access[kind][name] || "none"}
                    onChange={event => pick(kind, name, event.currentTarget.value as Choice)}>
                <option value="none">No access</option>
                <option value="read">Read</option>
                <option value="write">Read and write</option>
            </select>
        </div>
    );
    const systemGroups = groups.filter(group => group.system);
    const [filter, setFilter] = useState("");
    const shownSystem = filter
        ? systemGroups.filter(group => group.name.toLowerCase().includes(filter.trim().toLowerCase()))
        : systemGroups;
    return (
        <div class="field">
            <span class="field-label">Who can use it</span>
            <label class="choice">
                <input type="radio" name="access" checked={everyone} onChange={() => setEveryone(true)} />
                <span><strong>Everyone on the home network</strong><br /><span class="muted small">
                    Every person and every allowed NFS computer reads and writes every file. Best for media and family files.</span></span>
            </label>
            <label class="choice">
                <input type="radio" name="access" checked={!everyone} onChange={() => setEveryone(false)} />
                <span><strong>Chosen people and groups</strong><br /><span class="muted small">
                    Only those listed, each with read or read and write. Over NFS this needs each computer's user IDs to
                    match this NAS's.</span></span>
            </label>
            {!everyone && (
                <div class="stack">
                    {users.map(user => row("users", user.name, user.full_name))}
                    {groups.filter(group => !group.system)
                        .map(group => row("groups", group.name, group.members.join(", ") || "no members"))}
                    {systemGroups.length > 0 && (
                        <details class="system-groups">
                            <summary>Every other group on this NAS ({systemGroups.length})</summary>
                            <p class="muted small">Every group the machine has, so an app that already runs here can be
                                given the files it needs: Plex and the *arr apps usually read as video or render, a
                                container as docker, a backup tool as whatever it was installed with.</p>
                            <p class="muted small">root and sudo need no grant: root already reads anything, and its
                                members could change the files anyway.</p>
                            {systemGroups.length > 8 && (
                                <input class="group-filter" type="search" placeholder="Filter groups"
                                       aria-label="Filter groups"
                                       onInput={event => setFilter(event.currentTarget.value)} />
                            )}
                            {shownSystem.map(group => row("groups", group.name,
                                                          [group.members.join(", "),
                                                           group.private ? "own group" : ""].filter(Boolean).join(", ")
                                                          || "no members"))}
                            {!shownSystem.length && <p class="muted small">No group matches that.</p>}
                        </details>
                    )}
                    {!users.length && <p class="muted small">No people yet: add them in Users.</p>}
                </div>
            )}
        </div>
    );
}

export const nobody = (access: Access) => !Object.keys(access.users).length && !Object.keys(access.groups).length;

export function CreateShare({ onCreated }: { onCreated: (name: string) => void }) {
    const pools = useLoad(() => fnasJson<Pool[]>(["pool", "list"]));
    const users = useLoad(() => fnasJson<User[]>(["user", "list"]));
    const groups = useLoad(() => fnasJson<Group[]>(["group", "list", "--system"]));
    const [name, setName] = useState("");
    const [pool, setPool] = useState("");
    const [everyone, setEveryone] = useState(true);
    const [access, setAccess] = useState<Access>({ users: {}, groups: {} });
    const [nfs, setNfs] = useState(true);
    const [smb, setSmb] = useState(true);
    // Off to begin with: a share is not reachable over HTTPS or SFTP until asked
    // for, since either is a new way in
    const [webdav, setWebdav] = useState(false);
    const [sftp, setSftp] = useState(false);
    const action = useAction();

    return (
        <Card title="Create a share">
            <Loading loaded={pools}>{() => {
                const writable = pools.data!.filter(p => !p.readonly);
                const noPool = !writable.length;
                const chosenPool = pool || writable[0]?.name || "";
                const problem = noPool ? "Shares need a pool. Create or import one on the Storage page first."
                    : !SHARE_NAME.test(name) ? "Use letters, numbers, - and _, up to 32 characters."
                    : !nfs && !smb && !webdav && !sftp ? "Choose at least one way to share it."
                    : !everyone && nobody(access) ? "Choose at least one person or group, or make it for everyone."
                    : "";
                return (
                    <form class="stack" onSubmit={event => {
                        event.preventDefault();
                        action.run(async () => {
                            const output = await fnas(["share", "create", name, chosenPool, ...accessArgs(everyone, access),
                                                       ...(nfs ? ["--nfs"] : []), ...(smb ? ["--smb"] : []),
                                                       ...(webdav ? ["--webdav"] : []),
                                                       ...(sftp ? ["--sftp"] : [])]);
                            onCreated(name);
                            setName("");
                            return output;
                        });
                    }}>
                        {noPool && <Notice>Shares need a pool. Create or import one on the Storage page first.</Notice>}
                        <fieldset class="plain" disabled={noPool}>
                        <div class="grid-2">
                            <Field label="Name" hint="What computers on the network see">
                                <input value={name} onInput={event => setName(event.currentTarget.value)} placeholder="media" />
                            </Field>
                            <Field label="Pool">
                                <select value={chosenPool} onChange={event => setPool(event.currentTarget.value)}>
                                    {writable.map(p => <option key={p.name} value={p.name}>{p.name}</option>)}
                                </select>
                            </Field>
                        </div>
                        <AccessEditor everyone={everyone} setEveryone={setEveryone} access={access} setAccess={setAccess}
                                      users={users.data || []} groups={groups.data || []} />
                        <div class="field">
                            <span class="field-label">Share with</span>
                            <label class="check">
                                <input type="checkbox" checked={smb} onChange={event => setSmb(event.currentTarget.checked)} />
                                <span>Windows, Mac and phones (SMB)</span>
                            </label>
                            <label class="check">
                                <input type="checkbox" checked={nfs} onChange={event => setNfs(event.currentTarget.checked)} />
                                <span>Linux computers (NFS)</span>
                            </label>
                            <label class="check">
                                <input type="checkbox" checked={webdav}
                                       onChange={event => setWebdav(event.currentTarget.checked)} />
                                <span>A phone app or a mapped drive (WebDAV, over HTTPS)</span>
                            </label>
                            <label class="check">
                                <input type="checkbox" checked={sftp}
                                       onChange={event => setSftp(event.currentTarget.checked)} />
                                <span>File transfer over SFTP, for scripts and phones</span>
                            </label>
                            <label class="check disabled">
                                <input type="checkbox" disabled />
                                <span>FTP <span class="muted small">(not built yet)</span></span>
                            </label>
                        </div>
                        <p class="muted small">Each share's SMB and NFS settings, such as read-only, guests, the recycle bin,
                            Time Machine and which computers can mount it, are under Settings once it exists.</p>
                        {problem && name && !noPool && <p class="muted small">{problem}</p>}
                        <div class="row">
                            <Button kind="primary" type="submit" busy={action.busy} disabled={!!problem}>Create share</Button>
                        </div>
                        </fieldset>
                        <Result outcome={action.outcome} />
                    </form>
                );
            }}</Loading>
        </Card>
    );
}

export function LanSubnet() {
    const current = useLoad(async () => (await fnas(["lan", "show"])).trim());
    const [value, setValue] = useState<string | null>(null);
    const action = useAction(current.reload);
    return (
        <Card title="Who can reach shares">
            <Loading loaded={current}>{() => (
                <form class="stack" onSubmit={event => {
                    event.preventDefault();
                    action.run(() => fnas(["lan", "set", value ?? current.data!]));
                }}>
                    <Field label="Home network" hint="Only computers in this private subnet can use shares, such as 192.168.1.0/24.">
                        <input value={value ?? current.data!} onInput={event => setValue(event.currentTarget.value)} />
                    </Field>
                    <div class="row">
                        <Button type="submit" busy={action.busy} disabled={value === null || value === current.data}>Save</Button>
                    </div>
                    <Result outcome={action.outcome} />
                </form>
            )}</Loading>
        </Card>
    );
}

/** NAS-wide SMB settings. */
export function SmbServer({ settings, reload }: { settings: SharingSettings["smb"]; reload: () => void }) {
    const [values, setValues] = useState(settings);
    const action = useAction(reload);
    const set = <K extends keyof typeof values>(key: K, value: (typeof values)[K]) => setValues({ ...values, [key]: value });
    return (
        <Card title="SMB settings for the whole NAS">
            <form class="stack" onSubmit={event => {
                event.preventDefault();
                action.run(async () => {
                    await fnas(["sharing", "smb", "--workgroup", values.workgroup, "--description", values.description,
                                "--min-protocol", values.min_protocol, "--encryption", values.encryption,
                                "--windows-discovery", onOff(values.windows_discovery),
                                "--mac-discovery", onOff(values.mac_discovery)]);
                    return "Saved the SMB settings";
                });
            }}>
                <div class="grid-2">
                    <Field label="Workgroup" hint="Windows' default is WORKGROUP">
                        <input value={values.workgroup} onInput={event => set("workgroup", event.currentTarget.value)} />
                    </Field>
                    <Field label="Description" hint="Shown next to the NAS when browsing">
                        <input value={values.description} onInput={event => set("description", event.currentTarget.value)} />
                    </Field>
                    <Field label="Oldest protocol allowed" hint="SMB 1 is never allowed: it's unsafe">
                        <select value={values.min_protocol}
                                onChange={event => set("min_protocol", event.currentTarget.value as "SMB2" | "SMB3")}>
                            <option value="SMB2">SMB 2 (Windows Vista and later, most devices)</option>
                            <option value="SMB3">SMB 3 (Windows 8 and later, current Macs)</option>
                        </select>
                    </Field>
                    <Field label="Encryption" hint="Guests can't connect when it's required: they have no password to encrypt with">
                        <select value={values.encryption}
                                onChange={event => set("encryption", event.currentTarget.value as "desired" | "required")}>
                            <option value="desired">When the computer supports it</option>
                            <option value="required">Always: computers that can't encrypt are refused</option>
                        </select>
                    </Field>
                </div>
                <label class="check">
                    <input type="checkbox" checked={values.windows_discovery}
                           onChange={event => set("windows_discovery", event.currentTarget.checked)} />
                    <span>Show the NAS in Windows' Network (wsdd2)</span>
                </label>
                <label class="check">
                    <input type="checkbox" checked={values.mac_discovery}
                           onChange={event => set("mac_discovery", event.currentTarget.checked)} />
                    <span>Show the NAS in the Mac's Finder (Bonjour)</span>
                </label>
                <div class="row"><Button kind="primary" type="submit" busy={action.busy}>Save</Button></div>
                <Result outcome={action.outcome} />
            </form>
        </Card>
    );
}

/** NAS-wide NFS settings. */
export function NfsServer({ settings, reload }: { settings: SharingSettings["nfs"]; reload: () => void }) {
    const [values, setValues] = useState(settings);
    const action = useAction(reload);
    return (
        <Card title="NFS settings for the whole NAS">
            <form class="stack" onSubmit={event => {
                event.preventDefault();
                action.run(async () => {
                    await fnas(["sharing", "nfs", "--v3", onOff(values.v3), "--threads", String(values.threads),
                                "--fixed-ports", onOff(values.fixed_ports)]);
                    return "Saved the NFS settings. The NFS server restarts, so mounted computers pause for a moment.";
                });
            }}>
                <label class="check">
                    <input type="checkbox" checked={values.v3} onChange={event => setValues({ ...values, v3: event.currentTarget.checked })} />
                    <span>Allow NFS version 3, for older computers and some hypervisors. Version 4 is always on.</span>
                </label>
                <label class="check">
                    <input type="checkbox" checked={values.fixed_ports}
                           onChange={event => setValues({ ...values, fixed_ports: event.currentTarget.checked })} />
                    <span>Fixed ports for firewalls (mountd 20048, statd 32765 and 32766, lockd 32768). Only version 3 needs
                        them; version 4 uses port 2049 alone.</span>
                </label>
                <Field label="Server threads" hint="More helps when many computers use NFS at once">
                    <input type="number" min={1} max={64} value={values.threads}
                           onInput={event => setValues({ ...values, threads: Number(event.currentTarget.value) })} />
                </Field>
                <div class="row"><Button kind="primary" type="submit" busy={action.busy}>Save</Button></div>
                <Result outcome={action.outcome} />
            </form>
        </Card>
    );
}
