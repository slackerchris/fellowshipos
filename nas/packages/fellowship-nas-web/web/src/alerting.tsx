// Where alerts go, shared by the Health page and the setup wizard
import { useState } from "preact/hooks";
import { Channels, fnas, fnasJson } from "./api";
import { Button, Card, Field, Loading, Result, useAction, useLoad } from "./ui";

function EmailForm({ current, onSaved }: { current: Channels["email"]; onSaved: () => void }) {
    const [to, setTo] = useState(current?.to || "");
    const [host, setHost] = useState(current?.host || "");
    const [port, setPort] = useState(String(current?.port || 587));
    const [user, setUser] = useState(current?.user || "");
    const [tls, setTls] = useState(current?.tls || "starttls");
    const [password, setPassword] = useState("");
    const action = useAction(onSaved);
    return (
        <form class="stack" onSubmit={event => {
            event.preventDefault();
            action.run(() => fnas(["alert", "email-set", "--to", to, "--host", host, "--port", port, "--user", user,
                                   "--tls", tls, ...(password ? ["--password-stdin"] : [])], password || undefined));
        }}>
            <div class="grid-2">
                <Field label="Send to"><input type="email" value={to} onInput={event => setTo(event.currentTarget.value)} /></Field>
                <Field label="Mail server" hint="Your provider's SMTP server, such as smtp.gmail.com">
                    <input value={host} onInput={event => setHost(event.currentTarget.value)} />
                </Field>
                <Field label="Port"><input type="number" value={port} onInput={event => setPort(event.currentTarget.value)} /></Field>
                <Field label="Security">
                    <select value={tls} onChange={event => setTls(event.currentTarget.value)}>
                        <option value="starttls">STARTTLS (usually port 587)</option>
                        <option value="on">TLS (usually port 465)</option>
                        <option value="off">None</option>
                    </select>
                </Field>
                <Field label="User name" hint="Leave empty if the server needs no login">
                    <input value={user} onInput={event => setUser(event.currentTarget.value)} autoComplete="off" />
                </Field>
                <Field label="Password" hint={current?.user && !current.needs_password ? "Leave empty to keep the saved one" : "Often an app password"}>
                    <input type="password" value={password} autoComplete="new-password"
                           onInput={event => setPassword(event.currentTarget.value)} />
                </Field>
            </div>
            <div class="row"><Button kind="primary" type="submit" busy={action.busy} disabled={!to || !host}>Save email</Button></div>
            <Result outcome={action.outcome} />
        </form>
    );
}

function NtfyForm({ current, onSaved }: { current: Channels["ntfy"]; onSaved: () => void }) {
    const [url, setUrl] = useState(current?.url || "");
    const [token, setToken] = useState("");
    const action = useAction(onSaved);
    return (
        <form class="stack" onSubmit={event => {
            event.preventDefault();
            action.run(() => fnas(["alert", "ntfy-set", url, ...(token ? ["--token-stdin"] : [])], token || undefined));
        }}>
            <Field label="Topic address" hint="Install the ntfy app, subscribe to a long, hard-to-guess topic, and paste its address.">
                <input value={url} placeholder="https://ntfy.sh/your-private-topic" onInput={event => setUrl(event.currentTarget.value)} />
            </Field>
            <Field label="Access token" hint="Only for servers that need one">
                <input type="password" value={token} autoComplete="off" onInput={event => setToken(event.currentTarget.value)} />
            </Field>
            <div class="row"><Button kind="primary" type="submit" busy={action.busy} disabled={!url}>Save ntfy</Button></div>
            <Result outcome={action.outcome} />
        </form>
    );
}

export function AlertSetup() {
    const channels = useLoad(() => fnasJson<Channels>(["alert", "show"]));
    const [editing, setEditing] = useState<"email" | "ntfy" | null>(null);
    const action = useAction(channels.reload);
    const saved = () => { setEditing(null); channels.reload(); };

    return (
        <Card title="Where alerts go" actions={
            <Button busy={action.busy} disabled={!channels.data || !Object.keys(channels.data).length}
                    onClick={() => action.run(() => fnas(["alert", "test"]))}>Send a test alert</Button>}>
            <Loading loaded={channels}>{() => {
                const { email, ntfy } = channels.data!;
                return (
                    <div class="stack">
                        <div class="row">
                            <span><strong>Email:</strong> {email ? `to ${email.to} through ${email.host}` : "off"}</span>
                            {email?.needs_password && <span class="small">needs its password again</span>}
                            <Button onClick={() => setEditing(editing === "email" ? null : "email")}>{email ? "Change" : "Set up"}</Button>
                            {email && <Button onClick={() => action.run(() => fnas(["alert", "email-off"]))}>Turn off</Button>}
                        </div>
                        {editing === "email" && <EmailForm current={email} onSaved={saved} />}
                        <div class="row">
                            <span><strong>Phone (ntfy):</strong> {ntfy ? ntfy.url : "off"}</span>
                            <Button onClick={() => setEditing(editing === "ntfy" ? null : "ntfy")}>{ntfy ? "Change" : "Set up"}</Button>
                            {ntfy && <Button onClick={() => action.run(() => fnas(["alert", "ntfy-off"]))}>Turn off</Button>}
                        </div>
                        {editing === "ntfy" && <NtfyForm current={ntfy} onSaved={saved} />}
                        <Result outcome={action.outcome} />
                    </div>
                );
            }}</Loading>
        </Card>
    );
}
