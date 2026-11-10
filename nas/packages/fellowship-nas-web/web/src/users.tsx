// People's accounts and groups. One password per person, for the desktop and every way to share.
import { Fragment } from "preact";
import { useContext, useState } from "preact/hooks";
import { FnasError, Group, SshKey, User, UserShare, fnas, fnasJson, onOff } from "./api";
import { SessionContext } from "./settings";
import { Badge, Button, Card, Confirm, Field, Loading, Notice, Page, Result, useAction, useLoad } from "./ui";

const USER_NAME = /^[a-z_][a-z0-9_-]{0,31}$/;

const KINDS = {
    shares: { title: "Shares only", text: "Uses shares from other computers. Refused by SSH, so no shell and no tunnels." },
    ssh: { title: "Shares and SSH", text: "Also logs in to the NAS over SSH." },
    sftp: { title: "Files over SFTP", text: "Moves files over SFTP, with no shell and only their shares." },
} as const;

function PasswordFields({ password, setPassword, again, setAgain, label = "Password" }: {
    password: string;
    setPassword: (value: string) => void;
    again: string;
    setAgain: (value: string) => void;
    label?: string;
}) {
    return (
        <>
            <div class="grid-2">
                <Field label={label} hint="At least 8 characters. Used for the desktop and every way to share.">
                    <input type="password" autoComplete="new-password" value={password}
                           onInput={event => setPassword(event.currentTarget.value)} />
                </Field>
                <Field label="Again">
                    <input type="password" autoComplete="new-password" value={again}
                           onInput={event => setAgain(event.currentTarget.value)} />
                </Field>
            </div>
            {again && password !== again && <p class="muted small">The passwords don't match.</p>}
        </>
    );
}

export const passwordProblem = (password: string, again: string) =>
    password.length < 8 ? "The password needs at least 8 characters."
    : password !== again ? "The passwords don't match." : "";

function GroupChoices({ groups, chosen, setChosen, own }: {
    groups: Group[]; chosen: string[]; setChosen: (names: string[]) => void; own?: string;
}) {
    // A person's own group follows their account, so it isn't a choice here
    const offered = groups.filter(group => !(own && group.private && group.name === own));
    const system = offered.filter(group => group.system);
    const managed = offered.filter(group => !group.system);
    const box = (group: Group) => (
        <label class="check" key={group.name}>
            <input type="checkbox" checked={chosen.includes(group.name)}
                   onChange={event => setChosen(event.currentTarget.checked ? [...chosen, group.name]
                       : chosen.filter(name => name !== group.name))} />
            <span>{group.name}</span>
        </label>
    );
    if (!groups.length) {
        return <p class="muted small">No groups yet. Groups are made below, and shares can be given to a whole group.</p>;
    }
    return (
        <div class="stack">
            {managed.length > 0 && <div class="row">{managed.map(box)}</div>}
            {system.length > 0 && (
                <details class="system-groups">
                    <summary>System groups ({system.length})</summary>
                    <p class="muted small">Every other group on this NAS, such as video or docker. Joining one gives
                        this person whatever those files are, and keeps everyone else who is already in it.</p>
                    <div class="row">{system.map(box)}</div>
                </details>
            )}
        </div>
    );
}

function KindChoice({ kind, setKind, admin, setAdmin }: {
    kind: User["kind"];
    setKind: (kind: User["kind"]) => void;
    admin: boolean;
    setAdmin: (admin: boolean) => void;
}) {
    return (
        <div class="field">
            <span class="field-label">What they can do</span>
            {(Object.keys(KINDS) as User["kind"][]).map(option => (
                <label class="choice" key={option}>
                    <input type="radio" name="kind" checked={kind === option} onChange={() => setKind(option)} />
                    <span><strong>{KINDS[option].title}</strong><br /><span class="muted small">{KINDS[option].text}</span></span>
                </label>
            ))}
            <label class="check">
                <input type="checkbox" checked={admin} onChange={event => setAdmin(event.currentTarget.checked)} />
                <span>Administrator: also manages the NAS from this desktop</span>
            </label>
        </div>
    );
}

/** A form to add someone, used by the Users app and by Setup. */
export function AddUser({ onAdded }: { onAdded: () => void }) {
    const groups = useLoad(() => fnasJson<Group[]>(["group", "list", "--system"]));
    const [name, setName] = useState("");
    const [fullName, setFullName] = useState("");
    const [kind, setKind] = useState<User["kind"]>("shares");
    const [admin, setAdmin] = useState(false);
    const [chosen, setChosen] = useState<string[]>([]);
    const [password, setPassword] = useState("");
    const [again, setAgain] = useState("");
    const action = useAction(onAdded);
    const problem = !USER_NAME.test(name) ? "User names use lowercase letters, numbers, - and _, and start with a letter."
        : passwordProblem(password, again);
    return (
        <Card title="Add someone">
            <form class="stack" onSubmit={event => {
                event.preventDefault();
                action.run(async () => {
                    const output = await fnas(["user", "create", name, "--full-name", fullName, "--kind", kind,
                                               ...(admin ? ["--admin"] : []), "--groups", chosen.join(","),
                                               "--password-stdin"], password);
                    setName(""); setFullName(""); setPassword(""); setAgain(""); setChosen([]); setAdmin(false);
                    return output;
                });
            }}>
                <div class="grid-2">
                    <Field label="User name" hint="What they type to log in, such as sam">
                        <input value={name} autoComplete="off" onInput={event => setName(event.currentTarget.value)} />
                    </Field>
                    <Field label="Full name">
                        <input value={fullName} onInput={event => setFullName(event.currentTarget.value)} />
                    </Field>
                </div>
                <PasswordFields password={password} setPassword={setPassword} again={again} setAgain={setAgain} />
                <KindChoice kind={kind} setKind={setKind} admin={admin} setAdmin={setAdmin} />
                <div class="field">
                    <span class="field-label">Groups</span>
                    <Loading loaded={groups}>{() => <GroupChoices groups={groups.data!} chosen={chosen} setChosen={setChosen} own={name} />}</Loading>
                </div>
                {problem && name && <p class="muted small">{problem}</p>}
                <div class="row"><Button kind="primary" type="submit" busy={action.busy} disabled={!!problem}>Add</Button></div>
                <Result outcome={action.outcome} />
            </form>
        </Card>
    );
}

function EditUser({ user, groups, onDone }: { user: User; groups: Group[]; onDone: () => void }) {
    const shares = useLoad(() => fnasJson<UserShare[]>(["user", "shares", user.name]));
    const [fullName, setFullName] = useState(user.full_name);
    const [kind, setKind] = useState(user.kind);
    const [admin, setAdmin] = useState(user.admin);
    const [chosen, setChosen] = useState(user.groups);
    const [password, setPassword] = useState("");
    const [again, setAgain] = useState("");
    const save = useAction(onDone);
    const setPasswordAction = useAction();
    return (
        <div class="stack">
            <Field label="Full name">
                <input value={fullName} onInput={event => setFullName(event.currentTarget.value)} />
            </Field>
            <KindChoice kind={kind} setKind={setKind} admin={admin} setAdmin={setAdmin} />
            <div class="field">
                <span class="field-label">Groups</span>
                <GroupChoices groups={groups} chosen={chosen} setChosen={setChosen} own={user.name} />
            </div>
            <div class="row">
                <Button kind="primary" busy={save.busy} onClick={() => save.run(() => fnas([
                    "user", "set", user.name, "--full-name", fullName, "--kind", kind, "--admin", onOff(admin),
                    "--groups", chosen.join(",")]))}>Save</Button>
                <Button onClick={onDone}>Close</Button>
            </div>
            <Result outcome={save.outcome} />
            <form class="stack" onSubmit={event => {
                event.preventDefault();
                setPasswordAction.run(async () => {
                    const output = await fnas(["user", "password", user.name, "--password-stdin"], password);
                    setPassword(""); setAgain("");
                    return output;
                });
            }}>
                <PasswordFields password={password} setPassword={setPassword} again={again} setAgain={setAgain}
                                label="New password" />
                <div class="row"><Button type="submit" busy={setPasswordAction.busy}
                                         disabled={!!passwordProblem(password, again)}>Set password</Button></div>
                <Result outcome={setPasswordAction.outcome} />
            </form>
            <SshKeys user={user} />
            <div class="field">
                <span class="field-label">Shares {user.name} can use</span>
                <Loading loaded={shares}>{() => shares.data!.length
                    ? <ul class="small">{shares.data!.map(share => (
                        <li key={share.name}><strong>{share.name}</strong>: {share.level === "write" ? "read and write" : "read"}
                            <span class="muted"> ({share.through === "own" ? "given to them" : share.through === "everyone"
                                ? "the share is for everyone" : `through ${share.through}`})</span></li>))}</ul>
                    : <p class="muted small">None yet. Shares are given to people in Shares.</p>}</Loading>
            </div>
        </div>
    );
}

function SshKeys({ user }: { user: User }) {
    const keys = useLoad(() => user.kind === "ssh" ? fnasJson<SshKey[]>(["user", "keys", user.name]) : Promise.resolve([]));
    const [text, setText] = useState<string | null>(null);
    const action = useAction(keys.reload);
    if (user.kind !== "ssh") {
        return <p class="muted small">SSH keys: {user.name} uses shares only, so SSH lets them nowhere.</p>;
    }
    return (
        <div class="field">
            <span class="field-label">SSH keys</span>
            <Loading loaded={keys}>{() => <>
                {!keys.data!.length && <p class="muted small">None: {user.name} logs in over SSH with their password, while
                    passwords are allowed in Settings → SSH.</p>}
                {keys.data!.map(key => <span key={key.line} class="small mono">{key.type} {key.fingerprint} {key.comment}</span>)}
                <textarea rows={3} placeholder="ssh-ed25519 AAAA… name@computer, one key per line"
                          value={text ?? keys.data!.map(key => key.line).join("\n")}
                          onInput={event => setText(event.currentTarget.value)} />
                <div class="row"><Button busy={action.busy} disabled={text === null}
                                         onClick={() => action.run(async () => {
                                             const output = await fnas(["user", "keys", user.name, "--set-stdin"], text!);
                                             setText(null);
                                             return output;
                                         })}>Save keys</Button></div>
                <Result outcome={action.outcome} />
            </>}</Loading>
        </div>
    );
}

/** fnas states the exact phrase and the shares lost, so ask it rather than guess. */
async function removalTerms(name: string): Promise<{ phrase: string; lost: string[] }> {
    try {
        await fnas(["user", "remove", name]);
    } catch (error) {
        const text = (error as FnasError).message;
        const phrase = /--confirm "([^"]+)"/.exec(text);
        if (phrase) {
            return { phrase: phrase[1], lost: text.split("\n").filter(line => line.startsWith("    ")).map(line => line.trim()) };
        }
        throw error;
    }
    throw new FnasError("fnas didn't ask for confirmation");
}

function UserList({ users, groups, reload, adding, setAdding }: {
    users: User[]; groups: Group[]; reload: () => void; adding: boolean; setAdding: (open: boolean) => void;
}) {
    const { user: me } = useContext(SessionContext);
    const [editing, setEditing] = useState<string | null>(null);
    const [menu, setMenu] = useState<string | null>(null);
    const [removing, setRemoving] = useState<{ name: string; phrase: string; lost: string[] } | null>(null);
    const [keepHome, setKeepHome] = useState(true);
    const action = useAction(reload);
    const admins = users.filter(user => user.admin).length;
    const locked = (user: User) => user.name === me || (user.admin && admins === 1);
    const why = (user: User) => user.name === me ? "You can't change this for yourself"
        : user.admin && admins === 1 ? "The only administrator has to stay" : undefined;
    return (
        <Card title="People" actions={<Button kind="primary" onClick={() => setAdding(!adding)}>+ Add someone</Button>}>
            <p class="muted small row-intro">Each person has one password, for this desktop and every way to share.</p>
            {adding && (
                <div class="create-block">
                    <AddUser onAdded={() => { setAdding(false); reload(); }} />
                </div>
            )}
            <div class="table-wrap">
                <table>
                    <thead><tr><th>Person</th><th>Can</th><th>Groups</th><th>Password</th><th /></tr></thead>
                    <tbody>
                        {users.map(user => (
                            <Fragment key={user.name}>
                                <tr>
                                    <td><span class="share-name">{user.name === me
                                        ? <strong>{user.name}</strong> : <strong>{user.name}</strong>}
                                        {user.name === me && <span class="muted small">(you)</span>}</span>
                                        {user.full_name && <><br /><span class="small muted">{user.full_name}</span></>}</td>
                                    <td>
                                        <span class="small">{KINDS[user.kind].title}</span>{" "}
                                        {user.admin && <Badge kind="good">administrator</Badge>}{" "}
                                        {!user.enabled && <Badge kind="warning">turned off</Badge>}
                                    </td>
                                    <td class="small">{user.groups.join(", ") || <span class="muted">none</span>}</td>
                                    <td class="small">{!user.password ? <Badge kind="warning">No password yet</Badge>
                                        : user.smb_password ? "Set" : <Badge kind="warning">Not set for shares</Badge>}</td>
                                    <td class="row-actions">
                                        <span class="menu-wrap">
                                            <button type="button" class="kebab" aria-haspopup="menu"
                                                    aria-expanded={menu === user.name} aria-label={`More for ${user.name}`}
                                                    onClick={() => setMenu(menu === user.name ? null : user.name)}>⋯</button>
                                            {menu === user.name && (
                                                <span class="row-menu" role="menu">
                                                    <button type="button" role="menuitem"
                                                            onClick={() => { setMenu(null); setEditing(editing === user.name ? null : user.name); }}>Change…</button>
                                                    <button type="button" role="menuitem" disabled={locked(user)} title={why(user)}
                                                            onClick={() => { setMenu(null); action.run(() => fnas(["user", "set", user.name, "--enabled",
                                                                                                                   onOff(!user.enabled)])); }}>
                                                        {user.enabled ? "Turn off" : "Turn on"}</button>
                                                    <button type="button" role="menuitem" class="danger-text" disabled={locked(user)} title={why(user)}
                                                            onClick={() => { setMenu(null); action.run(async () => {
                                                                setRemoving({ name: user.name, ...await removalTerms(user.name) });
                                                            }); }}>Remove…</button>
                                                </span>
                                            )}
                                        </span>
                                    </td>
                                </tr>
                                {editing === user.name && (
                                    <tr><td colSpan={5}>
                                        {!user.password ? <Notice>{user.name} has no password yet, as after restoring
                                            settings: passwords are never saved on a pool. Set one below.</Notice>
                                            : !user.smb_password && <Notice>{user.name} has no password for shares yet, as
                                            accounts made before the Users app don't. Set one below.</Notice>}
                                        <EditUser user={user} groups={groups} onDone={() => { setEditing(null); reload(); }} />
                                    </td></tr>
                                )}
                            </Fragment>
                        ))}
                    </tbody>
                </table>
            </div>
            <p class="muted small">You can't turn off or remove yourself, or the only administrator.</p>
            {!removing && <Result outcome={action.outcome} />}
            {removing && (
                <Confirm title="Remove" phrase={removing.phrase} busy={action.busy} outcome={action.outcome}
                         onCancel={() => { setRemoving(null); action.clear(); }}
                         onConfirm={typed => action.run(async () => {
                             const output = await fnas(["user", "remove", removing.name, "--confirm", typed,
                                                        ...(keepHome ? ["--keep-home"] : [])]);
                             setRemoving(null);
                             return output;
                         })}>
                    <p><strong>{removing.name}</strong> can no longer log in or use any share.</p>
                    {removing.lost.length
                        ? <><p>They are taken off these shares:</p><ul class="small mono">{removing.lost.map(share => <li key={share}>{share}</li>)}</ul></>
                        : <p class="muted small">No share lists them by name.</p>}
                    <label class="check">
                        <input type="checkbox" checked={keepHome} onChange={event => setKeepHome(event.currentTarget.checked)} />
                        <span>Keep their home folder on the system disk</span>
                    </label>
                </Confirm>
            )}
        </Card>
    );
}

function Groups({ groups, users, reload, creating, setCreating }: {
    groups: Group[]; users: User[]; reload: () => void; creating: boolean; setCreating: (open: boolean) => void;
}) {
    const [name, setName] = useState("");
    const [members, setMembers] = useState<string[]>([]);
    const [editing, setEditing] = useState<string | null>(null);
    const [menu, setMenu] = useState<string | null>(null);
    const [edited, setEdited] = useState<string[]>([]);
    const [newName, setNewName] = useState("");
    const action = useAction(reload);
    const managed = groups.filter(group => !group.system);
    const machine = groups.filter(group => group.system);
    const memberChoices = (chosen: string[], set: (names: string[]) => void) => (
        <div class="row">
            {users.map(user => (
                <label class="check" key={user.name}>
                    <input type="checkbox" checked={chosen.includes(user.name)}
                           onChange={event => set(event.currentTarget.checked ? [...chosen, user.name]
                               : chosen.filter(found => found !== user.name))} />
                    <span>{user.name}</span>
                </label>
            ))}
        </div>
    );
    return (
        <Card title="Groups" actions={<Button kind="primary" onClick={() => setCreating(!creating)}>+ New group</Button>}>
            <p class="muted small row-intro">A group lets you give a share to several people at once, such as family.</p>
            {creating && (
                <form class="stack create-block" onSubmit={event => {
                    event.preventDefault();
                    action.run(async () => {
                        const output = await fnas(["group", "create", name, "--members", members.join(",")]);
                        setName(""); setMembers([]); setCreating(false);
                        return output;
                    });
                }}>
                    <Field label="New group">
                        <input value={name} placeholder="family" onInput={event => setName(event.currentTarget.value)} />
                    </Field>
                    {memberChoices(members, setMembers)}
                    <div class="row"><Button kind="primary" type="submit" busy={action.busy}
                                             disabled={!USER_NAME.test(name)}>Add group</Button></div>
                </form>
            )}
            {!managed.length && <p class="muted">No groups yet. Use New group to make the first one.</p>}
            {managed.length > 0 && (
                <div class="table-wrap">
                    <table>
                        <thead><tr><th>Group</th><th>Members</th><th /></tr></thead>
                        <tbody>
                            {managed.map(group => (
                                <Fragment key={group.name}>
                                    <tr>
                                        <td><strong>{group.name}</strong></td>
                                        <td class="small">{group.members.join(", ") || <span class="muted">no members</span>}</td>
                                        <td class="row-actions">
                                            <span class="menu-wrap">
                                                <button type="button" class="kebab" aria-haspopup="menu"
                                                        aria-expanded={menu === group.name} aria-label={`More for ${group.name}`}
                                                        onClick={() => setMenu(menu === group.name ? null : group.name)}>⋯</button>
                                                {menu === group.name && (
                                                    <span class="row-menu" role="menu">
                                                        <button type="button" role="menuitem"
                                                                onClick={() => { setMenu(null); setEditing(editing === group.name ? null : group.name);
                                                                                 setEdited(group.members); setNewName(group.name); }}>Change…</button>
                                                        <button type="button" role="menuitem" class="danger-text"
                                                                onClick={() => { setMenu(null); action.run(() => fnas(["group", "remove", group.name])); }}>Remove…</button>
                                                    </span>
                                                )}
                                            </span>
                                        </td>
                                    </tr>
                                    {editing === group.name && (
                                        <tr class="share-open"><td colSpan={3}>
                                            <div class="stack share-panel">
                                                <span class="field-label">Members of {group.name}</span>
                                                {memberChoices(edited, setEdited)}
                                                <div class="row">
                                                    <Button kind="primary" busy={action.busy}
                                                            onClick={() => action.run(() => fnas(["group", "members", group.name, edited.join(",")]))}>
                                                        Save members</Button>
                                                </div>
                                                <div class="pick-row">
                                                    <input aria-label="New name" value={newName}
                                                           onInput={event => setNewName(event.currentTarget.value)} />
                                                    <Button busy={action.busy}
                                                            disabled={newName === group.name || !USER_NAME.test(newName)}
                                                            onClick={() => action.run(async () => {
                                                                const output = await fnas(["group", "rename", group.name, newName]);
                                                                setEditing(null);
                                                                return output;
                                                            })}>Rename</Button>
                                                </div>
                                            </div>
                                        </td></tr>
                                    )}
                                </Fragment>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
            <p class="muted small">Removing a group takes it off every share; its members keep their accounts.</p>
            {machine.length > 0 && (
                <details class="system-groups">
                    <summary>Every other group on this NAS ({machine.length})</summary>
                    <p class="muted small">These came with the system, and its packages expect them to stay. A share can
                        be given to one from the share's access settings, which is how Plex, the *arr apps and other
                        software already running here reach the files they need. They can't be renamed or removed.</p>
                    <div class="table-wrap">
                        <table>
                            <thead><tr><th>Group</th><th>ID</th><th>Members</th></tr></thead>
                            <tbody>
                                {machine.map(group => (
                                    <tr key={group.name}>
                                        <td><strong>{group.name}</strong>
                                            {group.private && <span class="muted small"> own group</span>}</td>
                                        <td class="small muted">{group.gid}</td>
                                        <td class="small">{group.members.join(", ") || <span class="muted">no members</span>}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </details>
            )}
            <Result outcome={action.outcome} />
        </Card>
    );
}

export function Users() {
    const users = useLoad(() => fnasJson<User[]>(["user", "list"]));
    const groups = useLoad(() => fnasJson<Group[]>(["group", "list", "--system"]));
    const [tab, setTab] = useState<"people" | "groups">("people");
    const [adding, setAdding] = useState(false);
    const [creating, setCreating] = useState(false);
    const reload = () => { users.reload(); groups.reload(); };
    return (
        <Page>
            <div class="segmented page-tabs" role="tablist">
                <button type="button" role="tab" aria-selected={tab === "people"} class={tab === "people" ? "current" : ""}
                        onClick={() => setTab("people")}>People</button>
                <button type="button" role="tab" aria-selected={tab === "groups"} class={tab === "groups" ? "current" : ""}
                        onClick={() => setTab("groups")}>Groups</button>
            </div>
            <Loading loaded={users}>{() => <Loading loaded={groups}>{() => tab === "people"
                ? <UserList users={users.data!} groups={groups.data!} reload={reload}
                            adding={adding} setAdding={setAdding} />
                : <Groups groups={groups.data!} users={users.data!} reload={reload}
                          creating={creating} setCreating={setCreating} />}</Loading>}</Loading>
        </Page>
    );
}
