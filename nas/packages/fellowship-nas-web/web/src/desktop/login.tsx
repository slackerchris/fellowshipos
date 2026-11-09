import { useEffect, useRef, useState } from "preact/hooks";
import { Session, login } from "../api";
import { Mark } from "./icons";

export function Login({ onLogin }: { onLogin: (session: Session) => void }) {
    const [user, setUser] = useState("");
    const [password, setPassword] = useState("");
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const first = useRef<HTMLInputElement>(null);
    useEffect(() => first.current?.focus(), []);

    return (
        <div class="login">
            <form class="login-card" onSubmit={async event => {
                event.preventDefault();
                setBusy(true);
                setError("");
                try {
                    onLogin(await login(user, password));
                } catch (failure) {
                    setError((failure as Error).message);
                    setPassword("");
                } finally {
                    setBusy(false);
                }
            }}>
                <div class="brand">
                    <Mark />
                    <div>
                        <h1>FellowshipOS</h1>
                        <span class="muted small">NAS · {window.location.hostname}</span>
                    </div>
                </div>
                <label class="field">
                    <span class="field-label">User name</span>
                    <input ref={first} value={user} autoComplete="username" autoCapitalize="none" spellcheck={false}
                           onInput={event => setUser(event.currentTarget.value)} />
                </label>
                <label class="field">
                    <span class="field-label">Password</span>
                    <input type="password" value={password} autoComplete="current-password"
                           onInput={event => setPassword(event.currentTarget.value)} />
                </label>
                {error && <div class="notice notice-error" role="alert">{error}</div>}
                <button type="submit" class="button button-primary" disabled={busy || !user || !password}>
                    {busy ? "Logging in…" : "Log in"}</button>
                <p class="small muted">Use your account on this NAS: the user name and password set in Users.</p>
            </form>
        </div>
    );
}
