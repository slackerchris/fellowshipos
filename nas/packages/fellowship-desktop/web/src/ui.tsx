import { ComponentChildren, render } from "preact";
import { useCallback, useEffect, useRef, useState } from "preact/hooks";

export function mount(App: () => preact.JSX.Element) {
    render(<App />, document.getElementById("app")!);
}

export interface Loaded<T> {
    data?: T;
    error?: string;
    loading: boolean;
    reload: () => void;
}

/** Load something when the page opens, and again whenever reload is called. */
export function useLoad<T>(load: () => Promise<T>): Loaded<T> {
    const [state, setState] = useState<{ data?: T; error?: string; loading: boolean }>({ loading: true });
    const [round, setRound] = useState(0);
    useEffect(() => {
        let current = true;
        setState(previous => ({ ...previous, loading: true }));
        load().then(data => current && setState({ data, loading: false }),
                    (error: Error) => current && setState({ error: error.message, loading: false }));
        return () => { current = false; };
    }, [round]);
    return { ...state, reload: useCallback(() => setRound(r => r + 1), []) };
}

export interface Outcome {
    kind: "done" | "error";
    text: string;
}

/** Run one change at a time, keeping what happened to show the user. */
export function useAction(after?: () => void) {
    const [busy, setBusy] = useState(false);
    const [outcome, setOutcome] = useState<Outcome | null>(null);
    const run = async (work: () => Promise<string | void>) => {
        setBusy(true);
        setOutcome(null);
        try {
            const text = await work();
            setOutcome(text ? { kind: "done", text: text.trim() } : null);
            after?.();
            return true;
        } catch (error) {
            setOutcome({ kind: "error", text: (error as Error).message });
            return false;
        } finally {
            setBusy(false);
        }
    };
    return { busy, outcome, run, clear: () => setOutcome(null) };
}

export function Page({ title, intro, children }: { title: string; intro?: string; children: ComponentChildren }) {
    return (
        <main class="page">
            <header class="page-header">
                <h1>{title}</h1>
                {intro && <p class="muted">{intro}</p>}
            </header>
            {children}
        </main>
    );
}

export function Card({ title, actions, children }: { title?: string; actions?: ComponentChildren; children: ComponentChildren }) {
    return (
        <section class="card">
            {(title || actions) && (
                <div class="card-header">
                    {title && <h2>{title}</h2>}
                    {actions && <div class="row">{actions}</div>}
                </div>
            )}
            {children}
        </section>
    );
}

export function Notice({ kind = "info", children }: { kind?: "info" | "done" | "warning" | "error"; children: ComponentChildren }) {
    return <div class={`notice notice-${kind}`} role={kind === "error" ? "alert" : "status"}>{children}</div>;
}

export function Result({ outcome }: { outcome: Outcome | null }) {
    if (!outcome) {
        return null;
    }
    return <Notice kind={outcome.kind}><pre>{outcome.text}</pre></Notice>;
}

export function Loading({ loaded, children }: { loaded: Loaded<unknown>; children: () => ComponentChildren }) {
    if (loaded.error) {
        return <Notice kind="error"><pre>{loaded.error}</pre></Notice>;
    }
    if (loaded.data === undefined) {
        return <p class="muted">Loading…</p>;
    }
    return <>{children()}</>;
}

type ButtonKind = "primary" | "danger" | "plain";

export function Button({ kind = "plain", busy, disabled, onClick, type = "button", children }: {
    kind?: ButtonKind;
    busy?: boolean;
    disabled?: boolean;
    onClick?: () => void;
    type?: "button" | "submit";
    children: ComponentChildren;
}) {
    return (
        <button type={type} class={`button button-${kind}`} disabled={disabled || busy} onClick={onClick}>
            {busy ? "Working…" : children}
        </button>
    );
}

export function Badge({ kind, children }: { kind: "good" | "warning" | "bad" | "neutral"; children: ComponentChildren }) {
    return <span class={`badge badge-${kind}`}>{children}</span>;
}

export function Usage({ used, total }: { used: number; total: number }) {
    const percent = total ? Math.round((used * 100) / total) : 0;
    const level = percent >= 90 ? "bad" : percent >= 80 ? "warning" : "good";
    return (
        <div class="usage" title={`${percent}% used`}>
            <div class={`usage-fill usage-${level}`} style={{ width: `${percent}%` }} />
        </div>
    );
}

export function Field({ label, hint, children }: { label: string; hint?: ComponentChildren; children: ComponentChildren }) {
    return (
        <label class="field">
            <span class="field-label">{label}</span>
            {children}
            {hint && <span class="field-hint">{hint}</span>}
        </label>
    );
}

/**
 * Ask for a typed phrase before something that can't be undone, the same
 * phrase fnas itself checks.
 */
export function Confirm({ title, phrase, children, busy, outcome, onConfirm, onCancel }: {
    title: string;
    phrase: string;
    children: ComponentChildren;
    busy: boolean;
    outcome: Outcome | null;
    onConfirm: (typed: string) => void;
    onCancel: () => void;
}) {
    const [typed, setTyped] = useState("");
    // The autofocus attribute only works when a page loads, not in a dialog added later
    const input = useRef<HTMLInputElement>(null);
    useEffect(() => input.current?.focus(), []);
    return (
        <div class="overlay" role="dialog" aria-modal="true" aria-label={title}>
            <form class="dialog" onSubmit={event => { event.preventDefault(); onConfirm(typed); }}>
                <h2>{title}</h2>
                {children}
                <Field label={`Type "${phrase}" to confirm`}>
                    <input ref={input} value={typed} onInput={event => setTyped(event.currentTarget.value)} />
                </Field>
                <Result outcome={outcome} />
                <div class="row row-end">
                    <Button onClick={onCancel}>Cancel</Button>
                    <Button kind="danger" type="submit" busy={busy} disabled={typed !== phrase}>{title}</Button>
                </div>
            </form>
        </div>
    );
}
