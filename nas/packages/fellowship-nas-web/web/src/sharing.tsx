// Share creation, the LAN subnet and SMB passwords, shared by the Shares page and the setup wizard
import { useState } from "preact/hooks";
import { Pool, fnas, fnasJson, localUsers } from "./api";
import { Button, Card, Field, Loading, Notice, Result, useAction, useLoad } from "./ui";

const SHARE_NAME = /^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$/;

export function CreateShare({ onCreated }: { onCreated: (name: string) => void }) {
    const pools = useLoad(() => fnasJson<Pool[]>(["pool", "list"]));
    const users = useLoad(localUsers);
    const [name, setName] = useState("");
    const [pool, setPool] = useState("");
    const [mode, setMode] = useState<"shared" | "matched">("shared");
    const [owner, setOwner] = useState("");
    const [nfs, setNfs] = useState(true);
    const [smb, setSmb] = useState(true);
    const action = useAction();

    return (
        <Card title="Create a share">
            <Loading loaded={pools}>{() => {
                const writable = pools.data!.filter(p => p.readonly === "off");
                if (!writable.length) {
                    return <Notice>Shares need a pool. Create or import one on the Storage page first.</Notice>;
                }
                const chosenPool = pool || writable[0].name;
                const chosenOwner = owner || users.data?.[0] || "";
                const problem = !SHARE_NAME.test(name) ? "Use letters, numbers, - and _, up to 32 characters."
                    : !nfs && !smb ? "Choose at least one way to share it."
                    : mode === "matched" && !chosenOwner ? "Choose who owns the files."
                    : "";
                return (
                    <form class="stack" onSubmit={event => {
                        event.preventDefault();
                        action.run(async () => {
                            const output = await fnas(["share", "create", name, chosenPool, "--mode", mode,
                                                       ...(mode === "matched" ? ["--owner", chosenOwner] : []),
                                                       ...(nfs ? ["--nfs"] : []), ...(smb ? ["--smb"] : [])]);
                            onCreated(name);
                            setName("");
                            return output;
                        });
                    }}>
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
                        <div class="field">
                            <span class="field-label">Who can change files</span>
                            <label class="choice">
                                <input type="radio" name="mode" checked={mode === "shared"} onChange={() => setMode("shared")} />
                                <span><strong>Everyone</strong><br /><span class="muted small">
                                    A shared folder: every user and protocol can edit every file. Best for media and family files.</span></span>
                            </label>
                            <label class="choice">
                                <input type="radio" name="mode" checked={mode === "matched"} onChange={() => setMode("matched")} />
                                <span><strong>One owner</strong><br /><span class="muted small">
                                    Files keep their real owners. NFS computers need the same user IDs as this NAS.</span></span>
                            </label>
                        </div>
                        {mode === "matched" && (
                            <Field label="Owner">
                                <select value={chosenOwner} onChange={event => setOwner(event.currentTarget.value)}>
                                    {(users.data || []).map(user => <option key={user} value={user}>{user}</option>)}
                                </select>
                            </Field>
                        )}
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
                        </div>
                        {problem && name && <p class="muted small">{problem}</p>}
                        <div class="row">
                            <Button kind="primary" type="submit" busy={action.busy} disabled={!!problem}>Create share</Button>
                        </div>
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

export function SmbPassword() {
    const users = useLoad(localUsers);
    const [user, setUser] = useState("");
    const [password, setPassword] = useState("");
    const [again, setAgain] = useState("");
    const action = useAction();
    return (
        <Card title="SMB passwords">
            <p class="muted small">Windows and Mac ask for a user name and password when they connect. Samba keeps its own
                list, so set one here for each person.</p>
            <Loading loaded={users}>{() => {
                const chosen = user || users.data![0] || "";
                return (
                    <form class="stack" onSubmit={event => {
                        event.preventDefault();
                        action.run(async () => {
                            const output = await fnas(["user", "smb-password", chosen, "--password-stdin"], password);
                            setPassword("");
                            setAgain("");
                            return output;
                        });
                    }}>
                        <div class="grid-2">
                            <Field label="User">
                                <select value={chosen} onChange={event => setUser(event.currentTarget.value)}>
                                    {users.data!.map(name => <option key={name} value={name}>{name}</option>)}
                                </select>
                            </Field>
                            <Field label="Password">
                                <input type="password" autoComplete="new-password" value={password}
                                       onInput={event => setPassword(event.currentTarget.value)} />
                            </Field>
                            <Field label="Password again">
                                <input type="password" autoComplete="new-password" value={again}
                                       onInput={event => setAgain(event.currentTarget.value)} />
                            </Field>
                        </div>
                        {again && password !== again && <p class="muted small">The passwords don't match.</p>}
                        <div class="row">
                            <Button type="submit" busy={action.busy} disabled={!chosen || !password || password !== again}>
                                Set SMB password</Button>
                        </div>
                        <Result outcome={action.outcome} />
                    </form>
                );
            }}</Loading>
        </Card>
    );
}
