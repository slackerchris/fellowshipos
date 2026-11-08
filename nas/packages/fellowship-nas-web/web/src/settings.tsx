import { createContext } from "preact";
import { useContext, useState } from "preact/hooks";
import { Session, SessionInfo, SystemInfo, endSession, fnas, fnasJson, sessions, when } from "./api";
import { Badge, Button, Card, Confirm, Field, Loading, Notice, Page, Result, useAction, useLoad } from "./ui";

export const SessionContext = createContext<Session>({ id: "", user: "", admin: false, csrf: "" });

function OwnSmbPassword() {
    const { user } = useContext(SessionContext);
    const [password, setPassword] = useState("");
    const [again, setAgain] = useState("");
    const action = useAction();
    return (
        <Card title="SMB password">
            <p class="muted small">The password Windows, Mac and phones ask for when you open a share as <strong>{user}</strong>.
                It can differ from the one you log in here with.</p>
            <form class="stack" onSubmit={event => {
                event.preventDefault();
                action.run(async () => {
                    const output = await fnas(["user", "smb-password", user, "--password-stdin"], password);
                    setPassword("");
                    setAgain("");
                    return output;
                });
            }}>
                <div class="grid-2">
                    <Field label="New SMB password">
                        <input type="password" autoComplete="new-password" value={password}
                               onInput={event => setPassword(event.currentTarget.value)} />
                    </Field>
                    <Field label="Again">
                        <input type="password" autoComplete="new-password" value={again}
                               onInput={event => setAgain(event.currentTarget.value)} />
                    </Field>
                </div>
                {again && password !== again && <p class="muted small">The passwords don't match.</p>}
                <div class="row">
                    <Button type="submit" busy={action.busy} disabled={!password || password !== again}>Set SMB password</Button>
                </div>
                <Result outcome={action.outcome} />
            </form>
        </Card>
    );
}

function Sessions() {
    const { admin } = useContext(SessionContext);
    const list = useLoad(sessions);
    const action = useAction(list.reload);
    const stamp = (text: string) => new Date(text).toLocaleString();
    return (
        <Card title={admin ? "Everyone logged in" : "Where you're logged in"} actions={<Button onClick={list.reload}>Refresh</Button>}>
            <p class="muted small">Sessions end after 30 minutes without use, and after 12 hours in any case.</p>
            <Loading loaded={list}>{() => (
                <div class="table-wrap">
                    <table>
                        <thead><tr>{admin && <th>User</th>}<th>From</th><th>Logged in</th><th>Last used</th><th /></tr></thead>
                        <tbody>
                            {list.data!.map((session: SessionInfo) => (
                                <tr key={session.id}>
                                    {admin && <td>{session.user} {session.admin && <Badge kind="neutral">admin</Badge>}</td>}
                                    <td class="mono small">{session.remote}</td>
                                    <td class="small">{stamp(session.started)}</td>
                                    <td class="small">{stamp(session.last_seen)}</td>
                                    <td class="actions">
                                        {session.current
                                            ? <Badge kind="good">this browser</Badge>
                                            : <Button busy={action.busy} onClick={() => action.run(async () => { await endSession(session.id); })}>End</Button>}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}</Loading>
            <Result outcome={action.outcome} />
        </Card>
    );
}

function uptime(seconds: number) {
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    return days ? `${days} day${days === 1 ? "" : "s"}, ${hours} hour${hours === 1 ? "" : "s"}` : `${hours} hour${hours === 1 ? "" : "s"}`;
}

function ThisNas({ info, reload }: { info: SystemInfo; reload: () => void }) {
    const zones = useLoad(() => fnasJson<string[]>(["system", "timezones"]));
    const [name, setName] = useState(info.hostname);
    const [zone, setZone] = useState(info.timezone);
    const rename = useAction(reload);
    const rezone = useAction(reload);
    return (
        <Card title="This NAS">
            <p class="small">Up for {uptime(info.uptime)}. The clock reads {when(info.time)}
                {info.ntp ? ", kept right over the internet." : ", but isn't synchronised: check the internet connection."}</p>
            <form class="stack" onSubmit={event => { event.preventDefault(); rename.run(() => fnas(["system", "hostname", name])); }}>
                <Field label="Name" hint="What computers on the network see, such as in \\NAME and NAME.local">
                    <input value={name} onInput={event => setName(event.currentTarget.value.toLowerCase())} />
                </Field>
                <div class="row"><Button type="submit" busy={rename.busy} disabled={name === info.hostname}>Rename</Button></div>
                <Result outcome={rename.outcome} />
            </form>
            <form class="stack" onSubmit={event => { event.preventDefault(); rezone.run(() => fnas(["system", "timezone", zone])); }}>
                <Field label="Time zone" hint="Snapshots and schedules follow it">
                    <Loading loaded={zones}>{() => (
                        <select value={zone} onChange={event => setZone(event.currentTarget.value)}>
                            {zones.data!.map(z => <option key={z} value={z}>{z.replace(/_/g, " ")}</option>)}
                        </select>
                    )}</Loading>
                </Field>
                <div class="row"><Button type="submit" busy={rezone.busy} disabled={zone === info.timezone}>Change time zone</Button></div>
                <Result outcome={rezone.outcome} />
            </form>
        </Card>
    );
}

function Certificate({ info, reload }: { info: SystemInfo; reload: () => void }) {
    const [pem, setPem] = useState("");
    const action = useAction(reload);
    const cert = info.certificate;
    return (
        <Card title="HTTPS certificate">
            {cert ? (
                <div class="stack">
                    <div class="row">
                        <span class="mono small">{cert.subject}</span>
                        {cert.self_signed ? <Badge kind="warning">self-signed</Badge> : <Badge kind="good">your own</Badge>}
                    </div>
                    <span class="small">Expires {when(cert.expires)}</span>
                    <span class="small muted mono wrap">SHA-256 {cert.fingerprint}</span>
                    {cert.self_signed && (
                        <p class="small muted">Browsers warn about a self-signed certificate. Check the fingerprint above matches the one
                            your browser shows, then accept it, or paste a certificate of your own below.</p>
                    )}
                </div>
            ) : <Notice kind="warning">No certificate found.</Notice>}
            <form class="stack" onSubmit={event => {
                event.preventDefault();
                action.run(async () => {
                    const output = await fnas(["system", "certificate", "--stdin"], pem);
                    setPem("");
                    return output;
                });
            }}>
                <Field label="Use your own" hint="Paste the certificate, any intermediate certificates, and its private key, in PEM form">
                    <textarea rows={6} class="mono small" value={pem} spellcheck={false}
                              placeholder={"-----BEGIN CERTIFICATE-----\n…\n-----BEGIN PRIVATE KEY-----\n…"}
                              onInput={event => setPem(event.currentTarget.value)} />
                </Field>
                <div class="row">
                    <Button kind="primary" type="submit" busy={action.busy} disabled={!pem.trim()}>Use this certificate</Button>
                    <Button busy={action.busy} onClick={() => action.run(() => fnas(["system", "certificate", "--self-signed"]))}>
                        Make a new self-signed one</Button>
                </div>
                <Result outcome={action.outcome} />
            </form>
        </Card>
    );
}

const POWER = {
    restart: { label: "Restart", phrase: "restart the NAS", text: "Shares and this desktop are unavailable for a minute or two." },
    shutdown: { label: "Shut down", phrase: "shut down the NAS", text: "Everything stops until someone switches the NAS on again." },
} as const;

function Power() {
    const [asking, setAsking] = useState<keyof typeof POWER | null>(null);
    const action = useAction();
    return (
        <Card title="Power">
            <div class="row">
                <Button onClick={() => setAsking("restart")}>Restart</Button>
                <Button kind="danger" onClick={() => setAsking("shutdown")}>Shut down</Button>
            </div>
            {!asking && <Result outcome={action.outcome} />}
            {asking && (
                <Confirm title={POWER[asking].label} phrase={POWER[asking].phrase} busy={action.busy} outcome={action.outcome}
                         onCancel={() => { setAsking(null); action.clear(); }}
                         onConfirm={typed => action.run(async () => {
                             const output = await fnas(["system", asking, "--confirm", typed]);
                             setAsking(null);
                             return output;
                         })}>
                    <p>{POWER[asking].text}</p>
                </Confirm>
            )}
        </Card>
    );
}

const SECTIONS = [
    { id: "account", label: "Your account", admin: false },
    { id: "sessions", label: "Sessions", admin: false },
    { id: "nas", label: "Name and time", admin: true },
    { id: "certificate", label: "Certificate", admin: true },
    { id: "power", label: "Power", admin: true },
] as const;

export function Settings() {
    const { admin } = useContext(SessionContext);
    const visible = SECTIONS.filter(section => admin || !section.admin);
    const [section, setSection] = useState<string>(visible[0].id);
    const info = useLoad(() => admin ? fnasJson<SystemInfo>(["system", "show"]) : Promise.resolve(null));
    return (
        <div class="split">
            <nav class="split-nav" aria-label="Settings">
                {visible.map(item => (
                    <button key={item.id} type="button" class={`split-item ${section === item.id ? "current" : ""}`}
                            aria-current={section === item.id} onClick={() => setSection(item.id)}>{item.label}</button>
                ))}
            </nav>
            <Page>
                {section === "account" && <OwnSmbPassword />}
                {section === "sessions" && <Sessions />}
                {section === "nas" && <Loading loaded={info}>{() => <ThisNas info={info.data!} reload={info.reload} />}</Loading>}
                {section === "certificate" && <Loading loaded={info}>{() => <Certificate info={info.data!} reload={info.reload} />}</Loading>}
                {section === "power" && <Power />}
            </Page>
        </div>
    );
}
