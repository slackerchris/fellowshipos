import { ComponentChildren, render } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import "./installer.css";
import {
    Disk, GiB, Hello, Layout, Machine, Progress, Settings, hello, install, layoutFor, machine, pair, progress, reboot,
    shortName, size,
} from "./api";
import { suggestUser } from "./user";

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

function Frame({ step, onStep, answered, title = "FellowshipOS", subtitle = "installer", children }: {
    step?: number; onStep?: (index: number) => void; answered?: boolean[];
    title?: string; subtitle?: string; children: ComponentChildren;
}) {
    return (
        <div class="installer">
            <main class="installer-card">
                <header class="brand">
                    <Mark />
                    <div>
                        <h1>{title}</h1>
                        <span class="muted small">{subtitle}</span>
                    </div>
                </header>
                {step !== undefined && (
                    <ol class="steps">
                        {STEPS.map((name, index) => {
                            const here = index === step;
                            const decided = !here && answered?.[index];
                            return (
                                <li key={name} class={here ? "current" : decided ? "done" : ""}>
                                    {/* Any step can be opened at any time, so the rail is the way round */}
                                    {onStep
                                        ? <button type="button" aria-current={here ? "step" : undefined}
                                                  onClick={() => onStep(index)}>{index + 1}. {name}</button>
                                        : <span>{index + 1}. {name}</span>}
                                </li>
                            );
                        })}
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
                    <span class="disk-mark" aria-hidden="true" />
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
    // The multi-purpose image has no pool to prepare for, so it never splits
    const split = !info.whole_disk && disk.size >= info.split_from;
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
            ) : info.whole_disk ? (
                <Notice>The system takes the whole disk. Keep shared files on other disks, set up later.</Notice>
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
    // The user name follows the name until it has been edited by hand. It still
    // follows while it holds the suggestion for the name as it was before this
    // keystroke, which covers typing the name for the first time and carrying
    // on with it, but never overwrites a name someone chose for themselves.
    const setName = (value: string) => {
        const follows = settings.user === "" || settings.user === suggestUser(settings.full_name);
        set(follows ? { full_name: value, user: suggestUser(value) } : { full_name: value });
    };
    return (
        <div class="stack">
            <div class="grid-2">
                <Field label={info.edition === "nas" ? "Name of this NAS" : "Name of this computer"}
                       hint="What other computers see on the network">
                    <input value={settings.hostname} onInput={event => set({ hostname: event.currentTarget.value.toLowerCase() })} />
                </Field>
                <Field label="Time zone">
                    <select value={settings.timezone} onChange={event => set({ timezone: event.currentTarget.value })}>
                        {info.timezones.map(zone => <option key={zone} value={zone}>{zone.replace(/_/g, " ")}</option>)}
                    </select>
                </Field>
                <Field label="Your name">
                    <input value={settings.full_name} autoComplete="name" onInput={event => setName(event.currentTarget.value)} />
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
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    return (
        <form class="stack" onSubmit={async event => {
            event.preventDefault();
            setBusy(true);
            setError("");
            try {
                // The server asks for the phrase naming the disk, so the request
                // itself says which one it erases
                await onInstall(`erase ${shortName(disk.path)}`);
            } catch (failure) {
                setError((failure as Error).message);
                setBusy(false);
            }
        }}>
            <table>
                <tbody>
                    <tr><th>Disk</th><td>{size(disk.size)} {disk.model} <span class="mono small">{shortName(disk.path)}</span>
                        {disk.contents?.length ? <><br /><span class="small warning-text">Holds {disk.contents.join(", ")}. It is erased.</span></> : null}</td></tr>
                    <tr><th>Layout</th><td>System {size(layout.system)}, room to grow {size(layout.reserve)}, swap {size(layout.swap)}
                        {layout.data ? `, data ${size(layout.data)} left empty` : ""}</td></tr>
                    <tr><th>Name</th><td>{settings.hostname}</td></tr>
                    <tr><th>Account</th><td>{settings.user}{settings.full_name ? ` (${settings.full_name})` : ""}, administrator</td></tr>
                    <tr><th>Time zone</th><td>{settings.timezone}</td></tr>
                </tbody>
            </table>
            {error && <Notice kind="error">{error}</Notice>}
            <div class="row"><button class="button button-danger" type="submit" disabled={busy}>
                {busy ? "Starting…" : `Erase ${shortName(disk.path)} and install`}</button></div>
        </form>
    );
}

function Installing({ state, edition, onRetry }: { state: Progress; edition?: string; onRetry: () => void }) {
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
                        the setup on screen finishes the machine{edition === "nas"
                            ? ", and the NAS desktop opens at https://NAME from any browser" : " and asks what it will do"}.</Notice>
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
        return <Frame step={3} title={info?.title} subtitle={info?.subtitle}>
            <Installing state={state} edition={info?.edition} onRetry={() => { setState(null); load(); }} />
        </Frame>;
    }
    if (error) {
        return <Frame><Notice kind="error">{error}</Notice></Frame>;
    }
    if (!info) {
        return <Frame><p class="muted">Looking at this machine…</p></Frame>;
    }

    const canInstall = !!disk && !!layout && aboutReady(settings, again);
    // One way on from each step, named after where it goes. The rail above is the
    // rest of the navigation: any step, in any order, and back without a button.
    const onFrom = step === 0 ? !!disk && !!layout : step === 1 ? !!layout : aboutReady(settings, again);
    // A step reads as answered only once everything before it is too, so the rail
    // shows how far along the install is rather than which boxes happen to be set
    const answers = [!!disk, !!layout, aboutReady(settings, again), canInstall];
    const answered = answers.map((_, index) => answers.slice(0, index + 1).every(Boolean));
    return (
        <Frame step={step} onStep={setStep} answered={answered}
               title={info.title} subtitle={info.subtitle}>
            {step === 0 && greeting.local && (greeting.remote_urls?.length ? (
                <p class="small muted">Installing from another computer? Open <span class="mono">{greeting.remote_urls[0]}</span> there,
                    check the certificate it shows, and enter the pairing code <strong class="mono">{greeting.code}</strong>.</p>
            ) : (
                <p class="small muted">To install from another computer, add <span class="mono">fellowship.installer.remote=:8443</span> to
                    this image's boot entry; the console then prints the address, the certificate's fingerprint and the pairing code.</p>
            ))}
            {step === 0 && <ChooseDisk info={info} chosen={disk?.path || ""} onChoose={chosen => { setDisk(chosen); setLayout(chosen.layout || null); }} />}
            {step === 1 && disk && layout && <ChooseSpace info={info} disk={disk} layout={layout} onLayout={setLayout} />}
            {step === 1 && !(disk && layout) && (
                <div class="stack">
                    <Notice kind="warning">There's no disk yet, so there's nothing to lay out.</Notice>
                    <div class="row"><button class="button" type="button" onClick={() => setStep(0)}>Choose a disk</button></div>
                </div>
            )}
            {step === 2 && (
                <AboutYou info={info} settings={settings} again={again} onChange={setSettings} onAgain={setAgain} />
            )}
            {step === 3 && canInstall && disk && layout && (
                <Confirm disk={disk} layout={layout} settings={settings} onInstall={async typed => {
                    await install({ ...settings, disk: disk.path, system: layout.system }, typed);
                    setState({ phase: "installing", steps: [], step: -1, log: [] });
                }} />
            )}
            {step === 3 && !canInstall && (
                <div class="stack">
                    <Notice kind="warning">Not everything is settled yet.</Notice>
                    <div class="row">
                        {!disk && <button class="button" type="button" onClick={() => setStep(0)}>Choose a disk</button>}
                        {!aboutReady(settings, again) && <button class="button" type="button" onClick={() => setStep(2)}>Finish your account</button>}
                    </div>
                </div>
            )}
            {step < 3 && (
                <div class="row nav">
                    <button class="button button-primary" type="button" disabled={!onFrom} onClick={() => setStep(step + 1)}>
                        {STEPS[step + 1]} →</button>
                </div>
            )}
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
