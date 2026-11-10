import { createContext } from "preact";
import { useContext, useState } from "preact/hooks";
import {
    CheckSchedule, Session, SessionInfo, SshStatus, SystemInfo, SystemStorage, endSession, fnas, fnasJson, humanSize, jobStarted,
    onOff, sessions, when,
} from "./api";
import { Mode, PALETTES, useAppearance } from "./appearance";
import { DesktopContext } from "./desktop/context";
import { Badge, Button, Card, Confirm, Field, Loading, Notice, Page, Result, Usage, useAction, useLoad } from "./ui";

export const SessionContext = createContext<Session>({ id: "", user: "", admin: false, csrf: "" });

function OwnPassword() {
    const { user } = useContext(SessionContext);
    const [password, setPassword] = useState("");
    const [again, setAgain] = useState("");
    const action = useAction();
    return (
        <Card title="Your password">
            <p class="muted small">One password for <strong>{user}</strong>: this desktop, and opening shares from Windows,
                Mac, phones and other computers.</p>
            <form class="stack" onSubmit={event => {
                event.preventDefault();
                action.run(async () => {
                    const output = await fnas(["user", "password", user, "--password-stdin"], password);
                    setPassword("");
                    setAgain("");
                    return output;
                });
            }}>
                <div class="grid-2">
                    <Field label="New password" hint="At least 8 characters">
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
                    <Button type="submit" busy={action.busy} disabled={password.length < 8 || password !== again}>
                        Change password</Button>
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

const GIB = 1024 ** 3;

/** The system volume, and growing it into the room the installer left. */
function SystemSpace() {
    const storage = useLoad(() => fnasJson<SystemStorage>(["system", "storage"]));
    const [amount, setAmount] = useState(4);
    const action = useAction(storage.reload);
    return (
        <Card title="System space">
            <Loading loaded={storage}>{() => {
                const s = storage.data!;
                const room = Math.floor(s.room / GIB);
                return (
                    <div class="stack">
                        <Usage used={s.used} total={s.size} />
                        <span class="small muted">{humanSize(s.used)} used of {humanSize(s.size)} for FellowshipOS and its apps</span>
                        {!s.lvm && <p class="small muted">The system uses its whole disk, so there is nothing to grow into.</p>}
                        {s.lvm && room < 1 && <p class="small muted">There is no room left to grow into.</p>}
                        {s.lvm && room >= 1 && (
                            <form class="stack" onSubmit={event => {
                                event.preventDefault();
                                action.run(async () => {
                                    await fnas(["system", "grow", amount >= room ? "all" : `${amount}G`]);
                                    return `The system now has ${Math.min(amount, room)} GiB more room.`;
                                });
                            }}>
                                <p class="small">The installer kept {humanSize(s.room)} spare beside the system. Growing takes a moment and
                                    needs no restart, but it can't be undone, so add what you need now and keep the rest for later.</p>
                                <Field label={`Add ${Math.min(amount, room)} GiB`}>
                                    <input type="range" min={1} max={room} value={Math.min(amount, room)}
                                           onInput={event => setAmount(Number(event.currentTarget.value))} />
                                </Field>
                                <div class="row"><Button kind="primary" type="submit" busy={action.busy}>Grow the system</Button></div>
                            </form>
                        )}
                        <Result outcome={action.outcome} />
                    </div>
                );
            }}</Loading>
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

function Ssh() {
    const status = useLoad(() => fnasJson<SshStatus>(["ssh", "show"]));
    const action = useAction(status.reload);
    return (
        <Card title="SSH">
            <Loading loaded={status}>{() => {
                const ssh = status.data!;
                const noKey = !ssh.admins_with_keys.length;
                return (
                    <div class="stack">
                        <p class="small">Only accounts set to <strong>Shares and SSH</strong> in Users can log in over SSH;
                            shares-only accounts are refused before they get a shell or a tunnel. Root can never log in
                            over SSH.</p>
                        <p class="small">May log in now: {ssh.allowed.length ? <strong>{ssh.allowed.join(", ")}</strong>
                            : "nobody"} · {ssh.running ? <Badge kind="good">running</Badge>
                            : <Badge kind={ssh.enabled ? "bad" : "neutral"}>{ssh.enabled ? "not running" : "off"}</Badge>}</p>
                        <label class="check">
                            <input type="checkbox" checked={ssh.enabled} disabled={action.busy}
                                   onChange={event => action.run(() => fnas(["ssh", "set", "--enabled", onOff(event.currentTarget.checked)]))} />
                            <span>SSH on</span>
                        </label>
                        <label class={`check ${noKey && ssh.passwords ? "disabled" : ""}`}>
                            <input type="checkbox" checked={ssh.passwords} disabled={action.busy || (noKey && ssh.passwords)}
                                   onChange={event => action.run(() => fnas(["ssh", "set", "--passwords", onOff(event.currentTarget.checked)]))} />
                            <span>Allow passwords over SSH{noKey && ssh.passwords
                                ? <span class="muted small"> (can't be turned off until an administrator who may use SSH has a
                                    key in Users, or nobody could get in)</span>
                                : !ssh.passwords && <span class="muted small"> (off: keys only)</span>}</span>
                        </label>
                        <Result outcome={action.outcome} />
                    </div>
                );
            }}</Loading>
        </Card>
    );
}

const DAYS = { mon: "Monday", tue: "Tuesday", wed: "Wednesday", thu: "Thursday", fri: "Friday", sat: "Saturday", sun: "Sunday" };
const WEEKS = ["first", "second", "third", "fourth"];
const EVERY = { weekly: "Every week", monthly: "Every month", quarterly: "Every three months (January, April, July, October)" };
const hourText = (hour: number) => `${String(hour).padStart(2, "0")}:00`;

/** Change when an engine fnas schedules checks its pools: how often, which day of the month, and the hour. */
function ScheduleForm({ row, done }: { row: CheckSchedule; done: () => void }) {
    const [every, setEvery] = useState(row.every!);
    const [week, setWeek] = useState(row.week!);
    const [day, setDay] = useState(row.day!);
    const [hour, setHour] = useState(row.hour!);
    const action = useAction();
    return (
        <form class="stack" onSubmit={event => {
            event.preventDefault();
            action.run(async () => {
                await fnas(["pool", "schedule", row.engine, "--every", every, "--week", String(week), "--day", day,
                            "--hour", String(hour)]);
                done();
            });
        }}>
            <div class="grid-2">
                <Field label="How often">
                    <select value={every} onChange={event => setEvery(event.currentTarget.value as typeof every)}>
                        {(row.choices || []).map(choice => <option key={choice} value={choice}>{EVERY[choice]}</option>)}
                    </select>
                </Field>
                {every !== "weekly" && <Field label="Which week">
                    <select value={week} onChange={event => setWeek(Number(event.currentTarget.value))}>
                        {WEEKS.map((name, index) => <option key={name} value={index + 1}>The {name}</option>)}
                    </select>
                </Field>}
                <Field label="Day">
                    <select value={day} onChange={event => setDay(event.currentTarget.value as typeof day)}>
                        {Object.entries(DAYS).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
                    </select>
                </Field>
                <Field label="Starting at" hint="A check takes hours on large pools; shares stay usable meanwhile">
                    <select value={hour} onChange={event => setHour(Number(event.currentTarget.value))}>
                        {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{hourText(h)}</option>)}
                    </select>
                </Field>
            </div>
            <div class="row">
                <Button type="button" onClick={done}>Cancel</Button>
                <Button kind="primary" type="submit" busy={action.busy}>Save</Button>
            </div>
            <Result outcome={action.outcome} />
        </form>
    );
}

/** One engine: whether its pools are checked by themselves, when, and a way to check them now. */
function ScheduleRow({ row, reload }: { row: CheckSchedule; reload: () => void }) {
    const [changing, setChanging] = useState(false);
    const action = useAction(reload);
    if (!row.available) {
        return (
            <div class="stack schedule-row">
                <div class="row"><strong>{row.title}</strong> <Badge kind="neutral">{row.reason}</Badge></div>
            </div>
        );
    }
    return (
        <div class="stack schedule-row">
            <div class="row">
                <strong>{row.title}</strong>{" "}
                {row.enabled ? <Badge kind="good">on</Badge> : <Badge kind="neutral">off</Badge>}
            </div>
            <p class="small">{row.enabled
                ? <>Checks every {row.title} pool by itself {row.description}.{row.next ? ` Next: ${when(row.next)}.` : ""}</>
                : <>Automatic checks are off. Health won't count a pool as overdue; start a check here or in Storage
                    whenever you like.</>}
                {!row.timed && <span class="muted"> This is Debian's own array check, which pauses each morning and carries
                    on the next night, so here it only turns on or off.</span>}</p>
            <p class="small muted">{row.pools.length ? `Pools: ${row.pools.join(", ")}` : `No ${row.title} pools yet.`}</p>
            <label class="check">
                <input type="checkbox" checked={row.enabled} disabled={action.busy}
                       onChange={event => action.run(() => fnas(["pool", "schedule", row.engine, "--checks",
                                                                 onOff(event.currentTarget.checked)]))} />
                <span>Check automatically</span>
            </label>
            {changing
                ? <ScheduleForm row={row} done={() => { setChanging(false); reload(); }} />
                : <div class="row">
                    {row.timed && <Button disabled={!row.enabled} onClick={() => setChanging(true)}>Change schedule</Button>}
                    <Button busy={action.busy} disabled={!row.pools.length}
                            onClick={() => action.run(async () => jobStarted(await fnas(["pool", "scrub-all", row.engine]),
                                                                             "in Storage"))}>Check now</Button>
                </div>}
            {row.sync !== undefined && <ParitySync row={row} reload={reload} />}
            <Result outcome={action.outcome} />
        </div>
    );
}

/** An engine's nightly parity sync and disk spin-down, which sit beside its checks. */
function ParitySync({ row, reload }: { row: CheckSchedule; reload: () => void }) {
    const action = useAction(reload);
    const set = (...args: string[]) => action.run(() => fnas(["pool", "schedule", row.engine, ...args]));
    return (
        <div class="stack">
            <label class="check">
                <input type="checkbox" checked={row.sync} disabled={action.busy}
                       onChange={event => set("--sync", onOff(event.currentTarget.checked))} />
                <span>Sync parity every night{row.sync && row.sync_next ? ` (next: ${when(row.sync_next)})` : ""}
                    <span class="muted small"> Files changed since the last sync aren't protected yet. With the sync
                        off, parity is only brought up to date when you sync from Storage.</span></span>
            </label>
            <div class="grid-2">
                <Field label="Sync starts at" hint="Before the 03:00 backup, so the backup copies synced files">
                    <select value={row.sync_hour} disabled={!row.sync || action.busy}
                            onChange={event => set("--sync-hour", event.currentTarget.value)}>
                        {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{hourText(h)}</option>)}
                    </select>
                </Field>
                <Field label="Spin down idle disks" hint="Saves power and noise; the first read after waits a few seconds">
                    <select value={row.spin_down} disabled={action.busy}
                            onChange={event => set("--spin-down", event.currentTarget.value)}>
                        {(row.spin_down_choices || []).map(minutes => (
                            <option key={minutes} value={minutes}>{minutes ? `After ${minutes} minutes` : "Never"}</option>
                        ))}
                    </select>
                </Field>
            </div>
            <Result outcome={action.outcome} />
        </div>
    );
}

function StorageChecks() {
    const rows = useLoad(() => fnasJson<CheckSchedule[]>(["pool", "schedules"]));
    return (
        <Card title="Storage checks" actions={<Button onClick={rows.reload}>Refresh</Button>}>
            <div class="stack">
                <p class="small">A check reads every block on a pool and compares it with its checksum, parity or other copy,
                    so damage is found while it can still be put right. Each kind of storage has its own schedule.</p>
                <Loading loaded={rows}>{() => <>{rows.data!.map(row => <ScheduleRow key={row.engine} row={row} reload={rows.reload} />)}</>}</Loading>
            </div>
        </Card>
    );
}

function SetupAgain() {
    const desktop = useContext(DesktopContext);
    return (
        <Card title="Setup">
            <p class="small">The step-by-step setup opened by itself the first time you logged in. Each of its steps lives in
                its own app too: Storage, Shares, Snapshots and backup, and Alerts.</p>
            <div class="row"><Button onClick={() => desktop.open("setup")}>Run setup again</Button></div>
        </Card>
    );
}

function NotBuilt({ title, phase, children }: { title: string; phase: number; children: preact.ComponentChildren }) {
    return (
        <Card title={title}>
            <Notice>Not built yet (Phase {phase}).</Notice>
            <p class="small">{children}</p>
        </Card>
    );
}

const SECTIONS = [
    { id: "account", label: "Your account", admin: false },
    { id: "look", label: "Appearance", admin: false },
    { id: "sessions", label: "Sessions", admin: false },
    { id: "nas", label: "Name and time", admin: true },
    { id: "space", label: "System space", admin: true },
    { id: "certificate", label: "Certificate", admin: true },
    { id: "network", label: "Network", admin: true },
    { id: "updates", label: "Updates", admin: true },
    { id: "checks", label: "Storage checks", admin: true },
    { id: "ssh", label: "SSH", admin: true },
    { id: "power", label: "Power", admin: true },
    { id: "setup", label: "Setup", admin: true },
] as const;

/** Light, dark or the system's own, and which of the themes to draw with. Both are
    kept in this browser, not on the NAS. */
function Look() {
    const look = useAppearance();
    return (
        <Card title="Appearance">
            <p class="muted small">Kept in this browser, so another computer or browser starts on Auto with the
                Fellowship theme.</p>
            <Field label="Light or dark">
                <div class="segmented look-mode" role="group" aria-label="Mode">
                    {(["system", "light", "dark"] as Mode[]).map(option => (
                        <button key={option} type="button" class={look.mode === option ? "current" : ""}
                                aria-pressed={look.mode === option}
                                onClick={() => look.setMode(option)}>
                            {option === "system" ? "Auto" : option[0].toUpperCase() + option.slice(1)}
                        </button>
                    ))}
                </div>
            </Field>
            <span class="field-label">Theme</span>
            <div class="palettes look-palettes" role="group" aria-label="Theme">
                {PALETTES.map(palette => (
                    <button key={palette.id} type="button" class={`palette ${look.palette === palette.id ? "current" : ""}`}
                            aria-pressed={look.palette === palette.id} onClick={() => look.setPalette(palette.id)}>
                        <span class="palette-swatch" aria-hidden="true"
                              style={{ background: `linear-gradient(135deg, ${palette.swatch[0]} 55%, ${palette.swatch[1]} 55%)` }} />
                        {palette.title}
                    </button>
                ))}
            </div>
        </Card>
    );
}

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
                {section === "account" && <OwnPassword />}
                {section === "look" && <Look />}
                {section === "sessions" && <Sessions />}
                {section === "nas" && <Loading loaded={info}>{() => <ThisNas info={info.data!} reload={info.reload} />}</Loading>}
                {section === "certificate" && <Loading loaded={info}>{() => <Certificate info={info.data!} reload={info.reload} />}</Loading>}
                {section === "space" && <SystemSpace />}
                {section === "network" && <NotBuilt title="Network" phase={15}>Choosing between an automatic address (DHCP)
                    and a fixed one, and setting DNS and the gateway, comes here. Until then the NAS takes its address
                    automatically; to keep it from changing, reserve it in your router.</NotBuilt>}
                {section === "updates" && <NotBuilt title="Updates" phase={15}>Seeing and installing updates comes here.
                    Debian security updates already install by themselves every day, without restarting; Health shows
                    how many updates are waiting and whether a restart is needed.</NotBuilt>}
                {section === "checks" && <StorageChecks />}
                {section === "ssh" && <Ssh />}
                {section === "power" && <Power />}
                {section === "setup" && <SetupAgain />}
            </Page>
        </div>
    );
}
