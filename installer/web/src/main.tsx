import { ComponentChildren, render } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import "./installer.css";
import {
    Disk, GiB, Hello, Layout, Machine, Progress, Settings, hello, install, layoutFor, machine, pair, progress, reboot,
    shortName, size,
} from "./api";

const STEPS = ["Disk", "Space", "You", "Install"];

function Mark() {
    return (
        <svg width="44" height="44" viewBox="0 0 48 48" aria-hidden="true">
            <rect x="2" y="2" width="44" height="44" rx="11" fill="#1b2923" />
            <rect x="2.6" y="2.6" width="42.8" height="42.8" rx="10.4" fill="none" stroke="#c5a465" stroke-width="1.2" />
            <path d="M8 35l9-13 5 7 6-11 12 17z" fill="#29392f" stroke="#efe7d5" stroke-width="1.8" stroke-linejoin="round" />
            <path d="M34 10l1.1 2.4 2.4 1.1-2.4 1.1L34 17l-1.1-2.4-2.4-1.1 2.4-1.1z" fill="#c5a465" />
        </svg>
    );
}

function Frame({ step, children }: { step?: number; children: ComponentChildren }) {
    return (
        <div class="installer">
            <main class="installer-card">
                <header class="brand">
                    <Mark />
                    <div>
                        <h1>FellowshipOS</h1>
                        <span class="muted small">NAS installer</span>
                    </div>
                </header>
                {step !== undefined && (
                    <ol class="steps">
                        {STEPS.map((name, index) => (
                            <li key={name} class={index === step ? "current" : index < step ? "done" : ""}>{index + 1}. {name}</li>
                        ))}
                    </ol>
                )}
                {children}
            </main>
        </div>
    );
}

function Notice({ kind = "info", children }: { kind?: "info" | "done" | "warning" | "error"; children: ComponentChildren }) {
    return <div class={`notice notice-${kind}`} role={kind === "error" ? "alert" : "status"}>{children}</div>;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ComponentChildren }) {
    return (
        <label class="field">
            <span class="field-label">{label}</span>
            {children}
            {hint && <span class="field-hint">{hint}</span>}
        </label>
    );
}

function Pairing({ onPaired }: { onPaired: () => void }) {
    const [code, setCode] = useState("");
    const [error, setError] = useState("");
    return (
        <Frame>
            <form class="stack" onSubmit={async event => {
                event.preventDefault();
                try {
                    await pair(code);
                    onPaired();
                } catch (failure) {
                    setError((failure as Error).message);
                }
            }}>
                <p>To install from this computer, type the pairing code shown on the NAS's own screen.</p>
                <Field label="Pairing code">
                    <input value={code} inputMode="numeric" autoComplete="off" maxLength={6}
                           onInput={event => setCode(event.currentTarget.value)} />
                </Field>
                {error && <Notice kind="error">{error}</Notice>}
                <div class="row"><button class="button button-primary" type="submit" disabled={code.length !== 6}>Continue</button></div>
            </form>
        </Frame>
    );
}

function busName(bus: string) {
    return ({ sata: "SATA", nvme: "NVMe", usb: "USB", sas: "SAS", virtio: "Virtual", ata: "ATA" } as Record<string, string>)[bus] || bus.toUpperCase();
}

function ChooseDisk({ info, chosen, onChoose }: { info: Machine; chosen: string; onChoose: (disk: Disk) => void }) {
    const usable = info.disks.filter(disk => !disk.refused);
    return (
        <div class="stack">
            <p>Choose the disk for FellowshipOS. Only this disk is changed; every other disk stays as it is.</p>
            {!usable.length && <Notice kind="warning">No disk can be used. FellowshipOS needs a disk of at least {size(info.min_system + 5 * GiB)} that isn't the one it's running from.</Notice>}
            {info.disks.map(disk => (
                <button type="button" key={disk.path} class={`disk ${chosen === disk.path ? "chosen" : ""}`}
                        disabled={!!disk.refused} aria-pressed={chosen === disk.path} onClick={() => onChoose(disk)}>
                    <span class="disk-name"><strong>{size(disk.size)}</strong> {disk.model || "Disk"}
                        <span class="badge">{busName(disk.bus || "")}</span></span>
                    <span class="mono small muted">{shortName(disk.path)}{disk.serial ? ` · ${disk.serial}` : ""}</span>
                    {disk.refused
                        ? <span class="small">{disk.refused}</span>
                        : disk.contents?.length
                            ? <span class="small warning-text">Holds {disk.contents.join(", ")}. It will be erased.</span>
                            : <span class="small muted">Empty</span>}
                    {disk.bus === "usb" && !disk.refused && <span class="small warning-text">USB disks can be unreliable as a system disk.</span>}
                </button>
            ))}
        </div>
    );
}

function LayoutBar({ layout }: { layout: Layout }) {
    const parts = [
        { key: "system", label: "System", bytes: layout.system },
        { key: "reserve", label: "Room to grow", bytes: layout.reserve },
        { key: "swap", label: "Swap", bytes: layout.swap },
        { key: "data", label: "Data", bytes: layout.data },
    ].filter(part => part.bytes > 0);
    return (
        <div class="stack">
            <div class="layout-bar" role="img" aria-label={parts.map(part => `${part.label} ${size(part.bytes)}`).join(", ")}>
                {parts.map(part => (
                    <div key={part.key} class={`layout-part layout-${part.key}`} style={{ flexGrow: Math.max(part.bytes, layout.disk * 0.04) }} />
                ))}
            </div>
            <div class="layout-legend">
                {parts.map(part => (
                    <span key={part.key}><i class={`layout-${part.key}`} />{part.label} <strong>{size(part.bytes)}</strong></span>
                ))}
            </div>
        </div>
    );
}

function ChooseSpace({ info, disk, layout, onLayout }: { info: Machine; disk: Disk; layout: Layout; onLayout: (layout: Layout) => void }) {
    const split = disk.size >= info.split_from;
    const [system, setSystem] = useState(layout.system);
    const [error, setError] = useState("");
    const latest = useRef(0);
    const maximum = Math.max(info.min_system, Math.floor((disk.size - 8 * GiB - layout.swap - GiB) / GiB) * GiB);

    const change = async (value: number) => {
        setSystem(value);
        const round = ++latest.current;
        try {
            const next = await layoutFor(disk.size, value);
            round === latest.current && onLayout(next);
            setError("");
        } catch (failure) {
            setError((failure as Error).message);
        }
    };

    return (
        <div class="stack">
            <LayoutBar layout={layout} />
            {split ? (
                <>
                    <Field label={`System: ${size(system)}`}
                           hint={`FellowshipOS needs about ${size(info.installed)} now. The suggestion leaves room for updates, older kernels kept for safety, and logs.`}>
                        <input type="range" min={info.min_system} max={maximum} step={GiB} value={system}
                               onInput={event => change(Number(event.currentTarget.value))} />
                    </Field>
                    <p class="small muted">
                        {layout.data
                            ? `The ${size(layout.data)} data part stays empty. Turn it into storage from the desktop whenever you like, on its own or alongside other disks.`
                            : "Nothing is left for data at this size: the rest is room for the system to grow."}
                    </p>
                    <p class="small muted">The system sits on LVM, so it can use its room to grow later from Settings.</p>
                </>
            ) : (
                <Notice>This disk is small, so FellowshipOS uses all of it. Keep shared files on other disks, set up from the desktop.</Notice>
            )}
            {error && <Notice kind="error">{error}</Notice>}
        </div>
    );
}

const HOST = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;
const USER = /^[a-z_][a-z0-9_-]{0,31}$/;

function AboutYou({ info, settings, again, onChange, onAgain }: {
    info: Machine;
    settings: Settings;
    again: string;
    onChange: (settings: Settings) => void;
    onAgain: (again: string) => void;
}) {
    const set = (change: Partial<Settings>) => onChange({ ...settings, ...change });
    return (
        <div class="stack">
            <div class="grid-2">
                <Field label="Name of this NAS" hint="What other computers see on the network">
                    <input value={settings.hostname} onInput={event => set({ hostname: event.currentTarget.value.toLowerCase() })} />
                </Field>
                <Field label="Time zone">
                    <select value={settings.timezone} onChange={event => set({ timezone: event.currentTarget.value })}>
                        {info.timezones.map(zone => <option key={zone} value={zone}>{zone.replace(/_/g, " ")}</option>)}
                    </select>
                </Field>
                <Field label="Your name">
                    <input value={settings.full_name} autoComplete="name" onInput={event => set({ full_name: event.currentTarget.value })} />
                </Field>
                <Field label="User name" hint="For logging in to the desktop and over SSH">
                    <input value={settings.user} autoComplete="username" autoCapitalize="none" spellcheck={false}
                           onInput={event => set({ user: event.currentTarget.value.toLowerCase() })} />
                </Field>
                <Field label="Password" hint="At least 8 characters">
                    <input type="password" value={settings.password} autoComplete="new-password"
                           onInput={event => set({ password: event.currentTarget.value })} />
                </Field>
                <Field label="Password again">
                    <input type="password" value={again} autoComplete="new-password" onInput={event => onAgain(event.currentTarget.value)} />
                </Field>
            </div>
            {settings.hostname && !HOST.test(settings.hostname) && <p class="small">The name may use lowercase letters, numbers and -.</p>}
            {settings.user && !USER.test(settings.user) && <p class="small">The user name may use lowercase letters, numbers, - and _, starting with a letter.</p>}
            {again && again !== settings.password && <p class="small">The passwords don't match.</p>}
        </div>
    );
}

function aboutReady(settings: Settings, again: string) {
    return HOST.test(settings.hostname) && USER.test(settings.user) && settings.password.length >= 8
        && again === settings.password && !!settings.timezone;
}

function Confirm({ disk, layout, settings, onInstall }: { disk: Disk; layout: Layout; settings: Settings; onInstall: (typed: string) => Promise<void> }) {
    const phrase = `erase ${shortName(disk.path)}`;
    const [typed, setTyped] = useState("");
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    return (
        <form class="stack" onSubmit={async event => {
            event.preventDefault();
            setBusy(true);
            setError("");
            try {
                await onInstall(typed);
            } catch (failure) {
                setError((failure as Error).message);
                setBusy(false);
            }
        }}>
            <table>
                <tbody>
                    <tr><th>Disk</th><td>{size(disk.size)} {disk.model} <span class="mono small">{shortName(disk.path)}</span></td></tr>
                    <tr><th>Layout</th><td>System {size(layout.system)}, room to grow {size(layout.reserve)}, swap {size(layout.swap)}
                        {layout.data ? `, data ${size(layout.data)} left empty` : ""}</td></tr>
                    <tr><th>Name</th><td>{settings.hostname}</td></tr>
                    <tr><th>Account</th><td>{settings.user}{settings.full_name ? ` (${settings.full_name})` : ""}, administrator</td></tr>
                    <tr><th>Time zone</th><td>{settings.timezone}</td></tr>
                </tbody>
            </table>
            <Notice kind="warning">
                Everything on <strong>{shortName(disk.path)}</strong> is erased
                {disk.contents?.length ? <>, including {disk.contents.join(", ")}</> : ""}. Other disks aren't touched.
            </Notice>
            <Field label={`Type "${phrase}" to install`}>
                <input value={typed} autoComplete="off" onInput={event => setTyped(event.currentTarget.value)} />
            </Field>
            {error && <Notice kind="error">{error}</Notice>}
            <div class="row"><button class="button button-danger" type="submit" disabled={typed !== phrase || busy}>
                {busy ? "Starting…" : "Erase and install"}</button></div>
        </form>
    );
}

function Installing({ state, onRetry }: { state: Progress; onRetry: () => void }) {
    const [restarting, setRestarting] = useState(false);
    const log = state.log || [];
    return (
        <div class="stack">
            <ol class="progress">
                {state.steps.map((step, index) => {
                    const status = state.phase === "done" || index < state.step ? "done"
                        : index === state.step ? (state.phase === "failed" ? "failed" : "current") : "";
                    return <li key={step} class={status}>{step}</li>;
                })}
            </ol>
            {state.phase === "installing" && <p class="muted small">This takes a few minutes. Keep the machine switched on.</p>}
            {state.phase === "failed" && (
                <>
                    <Notice kind="error"><strong>The install stopped.</strong> {state.error}</Notice>
                    <div class="row"><button class="button" type="button" onClick={onRetry}>Back to the summary</button></div>
                </>
            )}
            {state.phase === "done" && (
                <>
                    <Notice kind="done">FellowshipOS is installed. Remove the USB stick or disc, then restart. After the first start,
                        the setup on screen installs the NAS, and the desktop opens at https://NAME from any browser.</Notice>
                    <div class="row">
                        <button class="button button-primary" type="button" disabled={restarting}
                                onClick={() => { setRestarting(true); reboot().catch(() => {}); }}>
                            {restarting ? "Restarting…" : "Restart now"}</button>
                    </div>
                </>
            )}
            {log.length > 0 && (
                <details>
                    <summary class="small muted">Details</summary>
                    <pre class="log mono small">{log.slice(-40).join("\n")}</pre>
                </details>
            )}
        </div>
    );
}

function guessTimezone(zones: string[]) {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return zones.includes(zone) ? zone : zones.includes("UTC") ? "UTC" : zones[0] || "UTC";
}

function Installer({ greeting }: { greeting: Hello }) {
    const [info, setInfo] = useState<Machine | null>(null);
    const [error, setError] = useState("");
    const [step, setStep] = useState(0);
    const [disk, setDisk] = useState<Disk | null>(null);
    const [layout, setLayout] = useState<Layout | null>(null);
    const [settings, setSettings] = useState<Settings>({ disk: "", system: 0, hostname: "fellowship", timezone: "UTC",
                                                          user: "", full_name: "", password: "" });
    const [again, setAgain] = useState("");
    const [state, setState] = useState<Progress | null>(null);

    const load = () => machine().then(found => {
        setInfo(found);
        setSettings(previous => ({ ...previous, timezone: guessTimezone(found.timezones) }));
    }, failure => setError(failure.message));
    useEffect(() => { load(); }, []);

    // Pick up an install already running, such as after reloading the page
    useEffect(() => {
        let timer = 0;
        const poll = async () => {
            try {
                const next = await progress();
                if (next.phase !== "choose") {
                    setState(next);
                }
                if (next.phase === "installing") {
                    timer = window.setTimeout(poll, 1000);
                }
            } catch {
                timer = window.setTimeout(poll, 3000);
            }
        };
        if (!state || state.phase === "installing") {
            poll();
        }
        return () => window.clearTimeout(timer);
    }, [state?.phase]);

    if (state && state.phase !== "choose") {
        return <Frame step={3}><Installing state={state} onRetry={() => { setState(null); load(); }} /></Frame>;
    }
    if (error) {
        return <Frame><Notice kind="error">{error}</Notice></Frame>;
    }
    if (!info) {
        return <Frame><p class="muted">Looking at this machine…</p></Frame>;
    }

    const canNext = step === 0 ? !!disk && !!layout : step === 1 ? !!layout : step === 2 ? aboutReady(settings, again) : false;
    return (
        <Frame step={step}>
            {step === 0 && greeting.local && (greeting.addresses?.length ? (
                <p class="small muted">Prefer another computer? Open <span class="mono">http://{greeting.addresses[0]}</span> there and
                    enter the pairing code <strong class="mono">{greeting.code}</strong>.</p>
            ) : (
                <p class="small muted">To install from another computer instead, connect this machine to the network; its address
                    appears here. The pairing code is <strong class="mono">{greeting.code}</strong>.</p>
            ))}
            {step === 0 && <ChooseDisk info={info} chosen={disk?.path || ""} onChoose={chosen => { setDisk(chosen); setLayout(chosen.layout || null); }} />}
            {step === 1 && disk && layout && <ChooseSpace info={info} disk={disk} layout={layout} onLayout={setLayout} />}
            {step === 2 && (
                <AboutYou info={info} settings={settings} again={again} onChange={setSettings} onAgain={setAgain} />
            )}
            {step === 3 && disk && layout && (
                <Confirm disk={disk} layout={layout} settings={settings} onInstall={async typed => {
                    await install({ ...settings, disk: disk.path, system: layout.system }, typed);
                    setState({ phase: "installing", steps: [], step: -1, log: [] });
                }} />
            )}
            <div class="row nav">
                {step > 0 && <button class="button" type="button" onClick={() => setStep(step - 1)}>Back</button>}
                {step < 3 && <button class="button button-primary" type="button" disabled={!canNext} onClick={() => setStep(step + 1)}>Next</button>}
            </div>
        </Frame>
    );
}

function App() {
    const [greeting, setGreeting] = useState<Hello | null>(null);
    const [error, setError] = useState("");
    const check = () => hello().then(setGreeting, failure => setError(failure.message));
    useEffect(() => { check(); }, []);
    // The network often comes up after the installer, so the screen keeps asking for its address
    useEffect(() => {
        if (!greeting?.local || greeting.addresses?.length) {
            return;
        }
        const timer = window.setInterval(() => hello().then(setGreeting, () => {}), 5000);
        return () => window.clearInterval(timer);
    }, [greeting?.local, greeting?.addresses?.length]);
    if (error) {
        return <Frame><Notice kind="error">{error}</Notice></Frame>;
    }
    if (!greeting) {
        return <Frame><p class="muted">Starting…</p></Frame>;
    }
    if (!greeting.allowed) {
        return <Pairing onPaired={check} />;
    }
    return <Installer greeting={greeting} />;
}

render(<App />, document.getElementById("app")!);
