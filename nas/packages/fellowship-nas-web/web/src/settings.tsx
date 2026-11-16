import { createContext } from "preact";
import { useContext, useEffect, useState } from "preact/hooks";
import {
    CheckSchedule, NetworkState, Session, SessionInfo, SshStatus, SystemInfo, SystemStorage, UpdatesStatus,
    changeOwnPassword, endSession,
    fnas, fnasJson,
    humanSize, jobStarted,
    onOff, sessions, when,
} from "./api";
import { MODES, PALETTES, useAppearance } from "./appearance";
import { DesktopContext } from "./desktop/context";
import { Badge, Button, Card, Confirm, Field, Loading, Notice, Page, Result, Usage, useAction, useLoad } from "./ui";

export const SessionContext = createContext<Session>({ id: "", user: "", admin: false, csrf: "" });

function OwnPassword() {
    const { user } = useContext(SessionContext);
    const [current, setCurrent] = useState("");
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
                    const output = await changeOwnPassword(user, current, password);
                    setCurrent("");
                    setPassword("");
                    setAgain("");
                    return output;
                });
            }}>
                <Field label="Current password">
                    <input type="password" autoComplete="current-password" value={current}
                           onInput={event => setCurrent(event.currentTarget.value)} />
                </Field>
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
                    <Button type="submit" busy={action.busy}
                            disabled={!current || password.length < 8 || password !== again}>
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

/** Updates are looked for every day and announced; they install, and the NAS
    restarts, only when somebody asks here. */
function Updates() {
    const status = useLoad(() => fnasJson<UpdatesStatus>(["updates", "show"]));
    const check = useAction(status.reload);
    const install = useAction(status.reload);
    const restart = useAction();
    const [asking, setAsking] = useState<"install" | "restart" | null>(null);
    return (
        <Loading loaded={status}>{() => {
            const found = status.data!;
            const security = found.packages.filter(p => p.security).length;
            return (
                <div class="stack">
                    <Card title="Updates" actions={<Button busy={check.busy}
                        onClick={() => check.run(() => fnas(["updates", "check"]))}>Check now</Button>}>
                        <p class="muted small">The NAS looks for updates every day and tells you when new ones are
                            waiting. Nothing installs, and the NAS never restarts, until you ask here.</p>
                        <p class="small">Last checked: <strong>{found.checked ? when(found.checked) : "never"}</strong>
                            {found.installed ? <> · last installed {when(found.installed)}</> : null}</p>
                        {found.error && <Notice kind="warning">The last check couldn't reach every source: {found.error}</Notice>}
                        <Result outcome={check.outcome} />
                        {found.packages.length === 0 ? <p>Everything is up to date.</p> : (
                            <>
                                <p><strong>{found.packages.length} waiting</strong>
                                    {security ? <>, <Badge kind="warning">{security} for security</Badge></> : null}</p>
                                <table class="small">
                                    <thead><tr><th>Package</th><th>Now</th><th>New</th><th /></tr></thead>
                                    <tbody>{found.packages.map(p => (
                                        <tr key={p.name}><td class="mono">{p.name}</td><td class="mono">{p.from ?? "new"}</td>
                                            <td class="mono">{p.to}</td>
                                            <td>{p.security && <Badge kind="warning">security</Badge>}</td></tr>))}</tbody>
                                </table>
                                <div class="row"><Button kind="primary" onClick={() => setAsking("install")}>Install updates</Button></div>
                            </>
                        )}
                        {asking === "install" && (
                            <Confirm title="Install updates" phrase="install updates" busy={install.busy}
                                     outcome={install.outcome}
                                     onCancel={() => { setAsking(null); install.clear(); }}
                                     onConfirm={() => install.run(async () => {
                                         const output = jobStarted(await fnas(["updates", "install"]), "in the taskbar");
                                         setAsking(null);
                                         return output;
                                     })}>
                                <p>Installs the {found.packages.length} update{found.packages.length === 1 ? "" : "s"} listed,
                                    from the sources this NAS already uses. Shares keep working meanwhile. If a restart is
                                    needed afterwards, this page says so and waits for you.</p>
                            </Confirm>
                        )}
                        {asking !== "install" && <Result outcome={install.outcome} />}
                    </Card>
                    {found.restart.needed && (
                        <Card title="Restart needed">
                            <p class="small">Installed updates finish when the NAS restarts
                                {found.restart.packages.length ? <>: {found.restart.packages.join(", ")}</> : null}.
                                It won't restart by itself.</p>
                            <div class="row"><Button onClick={() => setAsking("restart")}>Restart now</Button></div>
                            {asking === "restart" ? (
                                <Confirm title={POWER.restart.label} phrase={POWER.restart.phrase} busy={restart.busy}
                                         outcome={restart.outcome}
                                         onCancel={() => { setAsking(null); restart.clear(); }}
                                         onConfirm={typed => restart.run(async () => {
                                             const output = await fnas(["system", "restart", "--confirm", typed]);
                                             setAsking(null);
                                             return output;
                                         })}>
                                    <p>{POWER.restart.text}</p>
                                </Confirm>
                            ) : <Result outcome={restart.outcome} />}
                        </Card>
                    )}
                </div>
            );
        }}</Loading>
    );
}

function Ssh() {
    const status = useLoad(() => fnasJson<SshStatus>(["ssh", "show"]));
    const action = useAction(status.reload);
    return (
        <Card title="SSH">
            <Loading loaded={status}>{() => {
                const ssh = status.data!;
                if (ssh.root_ssh !== undefined) {
                    return (
                        <div class="stack">
                            <p class="small">People with <strong>Files over SFTP</strong> allowed in Users can transfer
                                files over SFTP: no shell, nothing to forward, and just their shares. They sign in with
                                their NAS password, or with a key kept in Users. Root's SSH is separate — keys only,
                                and only root can turn it on at the console with
                                {" "}<span class="mono">fellowship-root-ssh</span>.</p>
                            <p class="small">May use SFTP now: {ssh.allowed.length ? <strong>{ssh.allowed.join(", ")}</strong>
                                : "nobody"} · {ssh.running ? <Badge kind="good">running</Badge>
                                : <Badge kind={ssh.enabled ? "bad" : "neutral"}>{ssh.enabled ? "not running" : "off"}</Badge>}</p>
                            <label class="check">
                                <input type="checkbox" checked={ssh.enabled} disabled={action.busy}
                                       onChange={event => action.run(() => fnas(["ssh", "set", "--enabled", onOff(event.currentTarget.checked)]))} />
                                <span>SFTP on{!ssh.enabled && ssh.root_ssh
                                    ? <span class="muted small"> (root's SSH stays on)</span> : null}</span>
                            </label>
                            <label class="check">
                                <input type="checkbox" checked={ssh.passwords} disabled={action.busy}
                                       onChange={event => action.run(() => fnas(["ssh", "set", "--passwords", onOff(event.currentTarget.checked)]))} />
                                <span>Allow passwords for SFTP{!ssh.passwords
                                    ? <span class="muted small"> (off: keys only)</span> : null}</span>
                            </label>
                            <p class="small">Root SSH: {ssh.root_ssh
                                ? <Badge kind="good">on, keys only</Badge>
                                : <Badge kind="neutral">off — root logs in at the console</Badge>}</p>
                            <Result outcome={action.outcome} />
                        </div>
                    );
                }
                const noKey = !ssh.admins_with_keys.length;
                return (
                    <div class="stack">
                        <p class="small">Only accounts set to <strong>Shares and SSH</strong> or
                            <strong>Files over SFTP</strong> in Users can log in over SSH. The first get a shell; the
                            second get file transfer only, with no shell and nothing to forward, and see just their
                            shares. Shares-only accounts are refused before they get either. Root can never log in
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

/** The LAN address, with a way back: a change rolls back by itself unless it
    is confirmed from the new address (docs/fellowship-nas-network-rollback-plan.md). */
function Network() {
    const state = useLoad(() => fnasJson<NetworkState>(["network", "show"], true));
    const [mode, setMode] = useState<"dhcp" | "static">("dhcp");
    const [address, setAddress] = useState("");
    const [gateway, setGateway] = useState("");
    const [dns, setDns] = useState("");
    const [seeded, setSeeded] = useState(false);
    const [asking, setAsking] = useState(false);
    const [now, setNow] = useState(Date.now() / 1000);
    const apply = useAction(state.reload);
    const confirm = useAction(state.reload);
    const pending = state.data?.pending ?? null;

    useEffect(() => {
        const found = state.data;
        if (found && !seeded) {
            setMode(found.mode === "static" ? "static" : "dhcp");
            setAddress(found.address && found.prefix !== null ? `${found.address}/${found.prefix}` : "");
            setGateway(found.gateway ?? "");
            setDns(found.dns.join(", "));
            setSeeded(true);
        }
    }, [state.data, seeded]);

    // While a change waits, the countdown ticks and the state is asked for
    // again, so a rollback or a confirm from another browser shows up here
    useEffect(() => {
        if (!pending) return;
        const tick = setInterval(() => setNow(Date.now() / 1000), 1000);
        const poll = setInterval(state.reload, 5000);
        return () => { clearInterval(tick); clearInterval(poll); };
    }, [pending?.deadline, state.reload]);

    const servers = dns.split(",").map(part => part.trim()).filter(Boolean).join(",");
    const ready = mode === "dhcp" || Boolean(address.trim() && gateway.trim() && servers);
    const args = mode === "dhcp" ? ["network", "apply", "--dhcp"]
        : ["network", "apply", "--address", address.trim(), "--gateway", gateway.trim(), "--dns", servers];
    const left = pending ? Math.max(0, Math.floor(pending.deadline - now)) : 0;
    return (
        <Card title="Network" actions={<Button onClick={state.reload}>Refresh</Button>}>
            <Loading loaded={state}>{() => {
                const found = state.data!;
                return (
                    <div class="stack">
                        {!found.interface && <Notice kind="warning">No LAN interface was found. If this NAS has several
                            network ports, root can pin the right one with <span class="mono">fnas network interface NAME</span>.</Notice>}
                        {found.interface && (
                            <p class="small">
                                <span class="mono">{found.interface}</span>
                                {" · "}{found.address ? <span class="mono">{found.address}/{found.prefix}</span> : "no address yet"}
                                {found.gateway ? <> via <span class="mono">{found.gateway}</span></> : null}
                                {" · "}<Badge kind="neutral">{found.mode === "static" ? "fixed address" : "DHCP"}</Badge>
                                {found.dns.length ? <> · DNS <span class="mono">{found.dns.join(", ")}</span></> : null}
                            </p>
                        )}
                        {found.lan && <p class="small muted">Shares are offered to the LAN subnet <span class="mono">{found.lan}</span>,
                            which follows the address here.</p>}
                        {pending && (
                            <Notice kind="warning">
                                <div class="stack">
                                    <p>A network change is waiting: <strong>{pending.address
                                        ? <span class="mono">{pending.address}</span> : "a DHCP lease"}</strong>.
                                        {" "}{left} second{left === 1 ? "" : "s"} to confirm, or the previous settings come back.</p>
                                    {pending.address && (
                                        <p>Open <a href={`https://${pending.address}/`} target="_blank" rel="noreferrer"
                                                   class="mono">https://{pending.address}/</a> and sign in there, then confirm.
                                            This page can't confirm a change to a different address.</p>
                                    )}
                                    <div class="row">
                                        <Button kind="primary" busy={confirm.busy} disabled={!pending.address}
                                                onClick={() => confirm.run(() => fnas(["network", "confirm"]))}>
                                            Confirm from here</Button>
                                    </div>
                                    <Result outcome={confirm.outcome} />
                                </div>
                            </Notice>
                        )}
                        <form class="stack" onSubmit={event => { event.preventDefault(); setAsking(true); }}>
                            <Field label="How the NAS gets its address">
                                <div class="segmented" role="group" aria-label="Address way">
                                    <button type="button" class={mode === "dhcp" ? "current" : ""}
                                            aria-pressed={mode === "dhcp"} onClick={() => setMode("dhcp")}>
                                        Automatically (DHCP)</button>
                                    <button type="button" class={mode === "static" ? "current" : ""}
                                            aria-pressed={mode === "static"} onClick={() => setMode("static")}>
                                        A fixed address</button>
                                </div>
                            </Field>
                            {mode === "static" && (
                                <div class="grid-2">
                                    <Field label="Address and prefix" hint="For example 192.168.1.50/24">
                                        <input class="mono" value={address} placeholder="192.168.1.50/24"
                                               onInput={event => setAddress(event.currentTarget.value)} />
                                    </Field>
                                    <Field label="Gateway" hint="Inside the address's subnet">
                                        <input class="mono" value={gateway} placeholder="192.168.1.1"
                                               onInput={event => setGateway(event.currentTarget.value)} />
                                    </Field>
                                    <Field label="DNS servers" hint="Comma-separated">
                                        <input class="mono" value={dns} placeholder="192.168.1.1"
                                               onInput={event => setDns(event.currentTarget.value)} />
                                    </Field>
                                </div>
                            )}
                            <div class="row">
                                <Button kind="primary" type="submit" busy={apply.busy}
                                        disabled={!ready || Boolean(pending) || !found.interface}>Apply</Button>
                            </div>
                        </form>
                        {asking && (
                            <Confirm title="Change the address" phrase="change the address" busy={apply.busy}
                                     outcome={apply.outcome}
                                     onCancel={() => { setAsking(false); apply.clear(); }}
                                     onConfirm={() => apply.run(async () => {
                                         // The answer may never arrive: the change can take the
                                         // NAS off this page's address, so the dialog closes either way
                                         try {
                                             return await fnas(args);
                                         } finally {
                                             setAsking(false);
                                         }
                                     })}>
                                <p>The NAS takes the new address at once, and this page may lose it.
                                    {mode === "static"
                                        ? <> Open <span class="mono">https://{address.trim().split("/")[0]}/</span> and
                                            sign in there to confirm within 120 seconds.</>
                                        : <> If the lease changes the address, read the new one on the NAS's own screen
                                            and sign in there to confirm within 120 seconds.</>}
                                    {" "}Without a confirm, the previous settings come back by themselves.</p>
                            </Confirm>
                        )}
                        {!asking && <Result outcome={apply.outcome} />}
                        {!pending && apply.outcome?.kind === "error" && (
                            <Notice>If the address changed, this page can't reach the NAS anymore.
                                {mode === "static" && address.trim()
                                    ? <> Open <span class="mono">https://{address.trim().split("/")[0]}/</span> and sign in there.</>
                                    : <> Read the new address on the NAS's own screen, or find its lease in your router.</>}
                                {" "}If nobody confirms, the previous settings come back by themselves and this page finds the NAS again.</Notice>
                        )}
                    </div>
                );
            }}</Loading>
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
            <p class="muted small">Saved with your account on this NAS, so it comes with you to another computer
                or browser. Auto follows the device.</p>
            <Field label="Light or dark">
                <div class="segmented look-mode" role="group" aria-label="Mode">
                    {MODES.map(option => (
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
                {section === "network" && <Network />}
                {section === "updates" && <Updates />}
                {section === "checks" && <StorageChecks />}
                {section === "ssh" && <Ssh />}
                {section === "power" && <Power />}
                {section === "setup" && <SetupAgain />}
            </Page>
        </div>
    );
}
