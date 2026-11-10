// Every change goes through fnas, which the server's helper runs for members
// of nas-admins. The helper decides who may do what; the desktop only hides
// what someone can't use.

export class FnasError extends Error {}

export interface Session {
    id: string;
    user: string;
    admin: boolean;
    csrf: string;
}

export interface SessionInfo {
    id: string;
    user: string;
    admin: boolean;
    remote: string;
    started: string;
    last_seen: string;
    current?: boolean;
}

let token = "";
let signedOut = () => {};

/** Remember the session's token, and what to do when the server ends it. */
export function setSession(session: Session | null, onSignedOut: () => void) {
    token = session?.csrf || "";
    signedOut = onSignedOut;
}

async function request<T>(method: "GET" | "POST", path: string, body?: unknown, background = false): Promise<T> {
    const headers: Record<string, string> = {};
    if (body !== undefined) {
        headers["Content-Type"] = "application/json";
    }
    if (method === "POST") {
        headers["X-Fellowship-Token"] = token;
    }
    if (background) {
        headers["X-Fellowship-Background"] = "1";
    }
    let response: Response;
    try {
        response = await fetch(path, { method, headers, credentials: "same-origin",
                                       body: body === undefined ? undefined : JSON.stringify(body) });
    } catch {
        throw new FnasError("The NAS didn't answer. Check that it's switched on and on the network.");
    }
    const text = await response.text();
    let data: { error?: string } & Record<string, unknown> = {};
    try {
        data = text ? JSON.parse(text) : {};
    } catch {
        data = { error: text || response.statusText };
    }
    if (response.status === 401 && path !== "/api/login") {
        signedOut();
    }
    if (!response.ok) {
        throw new FnasError(data.error || `${response.status} ${response.statusText}`);
    }
    return data as T;
}

export const login = (user: string, password: string) => request<Session>("POST", "/api/login", { user, password });
export const logout = () => request<unknown>("POST", "/api/logout");
export const currentSession = () => request<Session>("GET", "/api/session", undefined, true);
export const sessions = () => request<SessionInfo[]>("GET", "/api/sessions");
export const endSession = (id: string) => request<unknown>("POST", "/api/sessions/end", { id });

/** Run fnas and return what it printed. */
export async function fnas(args: string[], input?: string): Promise<string> {
    return (await request<{ output: string }>("POST", "/api/fnas", { args, input })).output;
}

/** fnas tells the command line how to follow a long job; the desktop shows its progress itself. */
export function jobStarted(output: string, where = "at the top of Storage") {
    return output.replace(/: follow it with fnas job show \S+/g,
                          `. Its progress shows ${where} and in the taskbar, and it carries on if you close this window.`);
}

export async function fnasJson<T>(args: string[], background = false): Promise<T> {
    const { output } = await request<{ output: string }>("POST", "/api/fnas", { args: ["--json", ...args] }, background);
    return JSON.parse(output) as T;
}

/** People's accounts. Service accounts like nas-share can't log in, so they aren't listed. */
export const localUsers = () => request<string[]>("GET", "/api/users");

export interface Disk {
    path: string;
    by_id: string | null;
    size: number;
    model: string;
    serial: string;
    state: "os" | "in-use" | "has-data" | "empty";
    reason: string;
    // A partition is the free part of the system disk, left by the installer
    kind: "disk" | "partition";
    // The drive bay, where the enclosure reports one
    bay: string | null;
}

export interface Pool {
    name: string;
    size: number;
    allocated: number;
    free: number;
    health: string;
    readonly: boolean;
    engine: string;
}

export interface LayoutOption {
    // single and raidz are ZFS's; raid5, raid6 and raid10 are traditional RAID's; both have mirror;
    // parity1 and parity2 are Unraid-style, with that many parity disks
    layout: "single" | "mirror" | "raidz1" | "raidz2" | "raidz3" | "raid5" | "raid6" | "raid10" | "parity1" | "parity2";
    width: number;
    groups: number;
    spares: number;
    usable: number;
    // Space lost because disks in a group differ in size
    unused: number;
    // Failed disks each group survives
    survives: number;
    recommended: boolean;
}

export interface Vdev {
    name: string;
    type: string;
    state: string;
    path: string;
    size: number;
    errors: number;
    note: string;
    disks?: Vdev[];
    // An engine's own name for a group, such as "Parity disks: 1"
    title?: string;
}

export interface Progress {
    state: string;
    progress: number;
    started: number;
    ended: number;
}

export interface PoolStatus {
    name: string;
    state: string;
    status: string;
    action: string;
    groups: Vdev[];
    spares: Vdev[];
    scan: (Progress & { function: "scrub" | "resilver" | "sync"; errors: number }) | null;
    expand: Progress | null;
    expandable: boolean;
    // The ways the pool can grow right now, where its engine decides that from its disks
    growable?: Growth["how"][];
    // When parity was last brought up to date, on an engine that syncs it
    last_sync?: number | null;
}

export interface Growth {
    how: "group" | "expand" | "mirror" | "spare" | "data" | "parity";
    target?: string;
    count: number;
    text: string;
}

export interface Job {
    id: string;
    kind: "resilver" | "scrub" | "expand" | "sync" | "backup";
    title: string;
    pool: string | null;
    state: "running" | "done" | "failed";
    progress: number;
    started: number;
    ended: number | null;
    error: string | null;
}

export interface SystemStorage {
    device: string;
    size: number;
    used: number;
    lvm: boolean;
    room: number;
}

/** The disks inside a group, or inside a disk being replaced. */
export function leaves(vdev: Vdev): Vdev[] {
    return vdev.disks ? vdev.disks.flatMap(leaves) : [vdev];
}

export interface ImportablePool {
    pool: string;
    id: string;
    state: string;
    status?: string;
    action?: string;
}

export type Policy = { hourly: number; daily: number; monthly: number };

export type Level = "read" | "write";

/** Who can use a share: users and groups, each with read or read and write. */
export interface Access {
    users: Record<string, Level>;
    groups: Record<string, Level>;
}

export interface SmbOptions {
    readonly: boolean;
    hidden: boolean;
    guest: boolean;
    recycle: boolean;
    recycle_days: number;
    timemachine: boolean;
    // In GB; 0 for no limit
    timemachine_size: number;
    previous_versions: boolean;
}

export interface NfsClient {
    host: string;
    access: "rw" | "ro";
}

export interface NfsOptions {
    // Empty: the home network, read-write
    clients: NfsClient[];
    readonly: boolean;
    mapping: "auto" | "all" | "root" | "none";
    async: boolean;
}

export interface Share {
    name: string;
    pool: string;
    engine: string;
    // Whether the share's engine keeps earlier versions
    snapshots: boolean;
    dataset: string;
    path: string;
    mode: "everyone" | "chosen";
    access: Access;
    nfs: boolean;
    smb: boolean;
    policy: Policy;
    smb_options: SmbOptions;
    nfs_options: NfsOptions;
    readonly: boolean;
}

export interface User {
    name: string;
    uid: number;
    full_name: string;
    // shares: shares only, no SSH; ssh: shares and SSH
    kind: "shares" | "ssh";
    admin: boolean;
    groups: string[];
    // Whether the account has a password at all, and one for SMB
    password: boolean;
    smb_password: boolean;
    enabled: boolean;
}

export interface Group {
    name: string;
    gid: number;
    members: string[];
    // A group that came with the system, such as video or wheel: it can be
    // granted a share and joined, but never renamed or removed from here
    system: boolean;
    // Named after an account, whose primary group it is
    private: boolean;
}

export interface UserShare {
    name: string;
    level: Level;
    through: string;
}

export interface SharingSettings {
    smb: {
        workgroup: string;
        description: string;
        min_protocol: "SMB2" | "SMB3";
        encryption: "desired" | "required";
        windows_discovery: boolean;
        mac_discovery: boolean;
    };
    nfs: { v3: boolean; threads: number; fixed_ports: boolean };
}

/** fnas arguments for a share's access: --everyone, or --user and --group pairs. */
export function accessArgs(everyone: boolean, access: Access): string[] {
    if (everyone) {
        return ["--everyone"];
    }
    return [...Object.entries(access.users).flatMap(([name, level]) => ["--user", `${name}=${level}`]),
            ...Object.entries(access.groups).flatMap(([name, level]) => ["--group", `${name}=${level}`])];
}

export const onOff = (value: boolean) => value ? "on" : "off";

export interface Problem {
    key: string;
    // critical: something is broken. high, medium and low: a warning, by how soon it
    // needs attention. warning: what older versions sent
    severity: "info" | "low" | "medium" | "high" | "critical" | "warning";
    title: string;
    message: string;
}

export interface DiskHealth {
    path: string;
    by_id: string | null;
    model: string;
    serial: string;
    size: number;
    state: Disk["state"];
    // The enclosure slot, where the backplane reports one
    bay: string | null;
    verdict: "healthy" | "watch" | "failing" | "unknown";
    notes: string[];
    temperature: number | null;
    hours: number | null;
}

export interface Check {
    key: string;
    section: string;
    title: string;
    // The most severe problem found, or na: doesn't apply yet, and detail says why
    state: "passed" | "na" | "low" | "medium" | "high" | "critical";
    detail: string;
    problems: Problem[];
    alerting: boolean;
}

export interface UnitStatus {
    unit: string;
    title: string;
    does: string;
    // off: turned off on purpose, as SSH can be in Settings
    state: "running" | "stopped" | "failed" | "missing" | "off";
    detail: string;
}

export interface SshStatus {
    enabled: boolean;
    passwords: boolean;
    running: boolean;
    // Accounts set to Shares and SSH, which may log in
    allowed: string[];
    admins_with_keys: string[];
}

export interface SshKey {
    type: string;
    fingerprint: string;
    comment: string;
    line: string;
}

export interface Protocol {
    name: string;
    title: string;
    for: string;
    shares: string[];
    state: "running" | "stopped" | "failed" | "missing" | "not-built" | "later";
    detail: string;
}

export interface SystemStatus {
    disk: { size: number; used: number };
    memory: { total: number; available: number };
    load: number[];
    // Percent of the processors busy, sampled over a moment
    cpu: number;
    cpus: number;
    uptime: number;
    temperature: number | null;
    updates: number | null;
    reboot: boolean;
    kernel: string;
    newest_kernel: string | null;
}

export interface PoolDetail extends Pool {
    // What the desktop calls the engine, such as Traditional RAID
    engine_title: string;
    shares: number;
    last_scrub: number | null;
    last_snapshot: number | null;
    history: PoolHistory;
    projection: Projection;
    backup: { target: string; last: number | null; error: string | null } | null;
}

export interface Health {
    time: number;
    hourly: { last: number | null; every: number };
    checks: Check[];
    problems: Problem[];
    services: UnitStatus[];
    protocols: Protocol[];
    system: SystemStatus;
    // Only the hourly check can add to these, so a new install starts empty
    history: { system: SystemHistory };
    pools: PoolDetail[];
    disks: DiskHealth[];
    alerts: { channels: Channels; last: Alert | null };
}

// [time, used, size]
export type UsagePoint = [number, number, number];

// [time, memory used, memory total, 1-minute load, system disk used, system disk size, °C or null]
export type SystemPoint = [number, number, number, number, number, number, number | null];

export interface PoolHistory {
    hourly: UsagePoint[];
    daily: UsagePoint[];
}

export interface SystemHistory {
    hourly: SystemPoint[];
    daily: SystemPoint[];
}

// When a pool fills, at the rate of its history: days left, or why it can't say
export interface Projection {
    days?: number;
    per_day?: number;
    reason?: string;
}

export interface PoolDetails {
    name: string;
    size: number;
    allocated: number;
    fragmentation: number | null;
    compression: number;
    // What the data would take without compression
    logical: number;
    created: number;
    last_scrub: number | null;
    // When parity was last brought up to date, on an engine that syncs it
    last_sync?: number | null;
    errors: number;
    space: {
        used: number;
        free: number;
        shares: { name: string; files: number; snapshots: number }[];
        parts: { shares: number; snapshots: number; backups: number; other: number };
    };
    engine: string;
    // An array's format, xfs or ext4; ZFS is its own
    filesystem: string | null;
    history: { hourly: UsagePoint[]; daily: UsagePoint[] };
    projection: { days?: number; per_day?: number; reason?: string };
}

export interface EngineFilesystem {
    name: string;
    title: string;
    recommended: boolean;
    text: string;
}

/** A storage engine, and how the desktop presents its pools, so no screen asks which engine it is. */
export interface Engine {
    name: "zfs" | "mergerfs" | "mdadm" | "btrfs";
    title: string;
    available: boolean;
    reason: string;
    summary: string;
    // An engine not built yet has none of these
    snapshots?: boolean;
    backup?: boolean;
    compresses?: boolean;
    repair?: boolean;
    grow?: Growth["how"][];
    // These may hold {pool}, {group} or {count}; fill() puts them in
    grow_text?: Partial<Record<Growth["how"], string>>;
    expand_text?: string;
    repair_notice?: string;
    repair_text?: string;
    check_button?: string;
    check_name?: string;
    check_text?: string;
    errors_label?: string;
    replace_text?: string;
    filesystems?: EngineFilesystem[];
}

/** One engine's row in Settings → Storage checks. An engine not built yet has only the first six. */
export interface CheckSchedule {
    engine: Engine["name"];
    title: string;
    available: boolean;
    reason: string;
    // Whether fnas sets the timing; Debian's own array check only turns on and off
    timed: boolean;
    pools: string[];
    enabled?: boolean;
    every?: "weekly" | "monthly" | "quarterly";
    // How often this engine can check
    choices?: ("weekly" | "monthly" | "quarterly")[];
    week?: number;
    day?: "mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun";
    hour?: number;
    description?: string;
    next?: number | null;
    // An engine with a parity sync: whether it runs nightly, its hour, and when idle disks spin down
    sync?: boolean;
    sync_hour?: number;
    sync_next?: number | null;
    spin_down?: number;
    spin_down_choices?: number[];
}

let engineList: Promise<Record<string, Engine>> | null = null;

/** Every engine by name. They change only with a new fnas, so this asks once per page load. */
export function loadEngines(): Promise<Record<string, Engine>> {
    engineList ??= fnasJson<Engine[]>(["pool", "engines"]).then(
        list => Object.fromEntries(list.map(engine => [engine.name, engine])),
        error => { engineList = null; throw error; });
    return engineList;
}

/** An engine's text with the pool's or group's name put in. */
export const fill = (text: string | undefined, names: { pool?: string; group?: string; count?: number }) =>
    (text || "").replace(/\{(pool|group|count)\}/g, (_, key: "pool" | "group" | "count") => String(names[key] ?? ""));

export interface SetupStatus {
    done: boolean;
    finished?: number;
    skipped?: boolean;
}

export interface BackupStatus {
    job: { source: string; target: string; since: number } | null;
    last_attempt?: number;
    last_success?: number;
    error?: string | null;
}

export interface BackupCopy {
    name: string;
    pool: string;
    dataset: string;
    snapshots: number;
}

export interface Snapshot {
    name: string;
    created: number;
    used: number;
}

export interface Alert {
    time: number;
    severity: Problem["severity"];
    title: string;
    message: string;
    undelivered: string[];
}

export interface Channels {
    email?: { to: string; host: string; port: number; user: string; tls: string; needs_password: boolean };
    ntfy?: { url: string; token: boolean };
}

export interface SavedConfig {
    install: string;
    from_this_install: boolean;
    saved: number;
    lan: string | null;
    shares: Share[];
    users: { name: string; uid: number }[];
    alerts: Channels;
    backup: { source: string; target: string } | null;
}

export const LAYOUT_NAMES: Record<LayoutOption["layout"], string> = {
    single: "Single disk",
    mirror: "Mirror",
    raidz1: "RAIDZ1",
    raidz2: "RAIDZ2",
    raidz3: "RAIDZ3",
    raid5: "RAID 5",
    raid6: "RAID 6",
    raid10: "RAID 10",
    parity1: "SnapRAID, 1 parity disk",
    parity2: "SnapRAID, 2 parity disks",
};

export function plural(count: number, word: string) {
    return `${count} ${word}${count === 1 ? "" : "s"}`;
}

export function humanSize(bytes: number): string {
    const units = ["B", "KB", "MB", "GB", "TB", "PB"];
    let value = bytes;
    let unit = 0;
    while (value >= 1000 && unit < units.length - 1) {
        value /= 1000;
        unit++;
    }
    return `${value.toFixed(value < 10 && unit ? 1 : 0)} ${units[unit]}`;
}

export function when(stamp: number): string {
    return new Date(stamp * 1000).toLocaleString();
}

/** A share the signed-in person can browse, and what they may do in it. Where it
    is on disk is the helper's business, so it never reaches the browser. */
export interface FileShare {
    share: string;
    level: "read" | "write";
    through: string;
    snapshots: boolean;
}

export interface FileEntry {
    name: string;
    dir: boolean;
    size: number;
    modified: string;
}

export const fileShares = () => request<{ shares: FileShare[] }>("GET", "/api/files");

export const folderIn = (share: string, path: string) =>
    request<{ share: string; path: string; folder: { entries: FileEntry[] } }>(
        "GET", `/api/files?share=${encodeURIComponent(share)}&path=${encodeURIComponent(path)}`);

/** Make a folder inside a share the signed-in person can change. */
export const makeFolder = (share: string, path: string, name: string) =>
    request<{ done: boolean }>("POST", "/api/files/mkdir", { share, path, name });

/** Rename or move something inside one share. to is the whole path it should
    have afterwards, not just the new name. */
export const renameFile = (share: string, path: string, to: string) =>
    request<{ done: boolean }>("POST", "/api/files/rename", { share, path, to });

/** Copy a file, or a folder with what it holds, to a new path in the same share. */
export const copyFile = (share: string, path: string, to: string) =>
    request<{ done: boolean }>("POST", "/api/files/copy", { share, path, to });

/** The folders in a share, for picking where to move something. cut says there were more than it lists. */
export const folderTree = (share: string) =>
    request<{ folders: string[]; cut: boolean }>("GET", `/api/files/tree?share=${encodeURIComponent(share)}&path=`);

export const deleteFile = (share: string, path: string, recursive = false) =>
    request<{ done: boolean }>("POST", "/api/files/delete", { share, path, recursive });

/** Send one file's bytes. The server passes them to the helper, which writes them
    as the signed-in person, so a refusal comes back in the same words as any
    other. */
export async function uploadFile(share: string, path: string, name: string, file: File,
                                 replace = false): Promise<number> {
    const query = `share=${encodeURIComponent(share)}&path=${encodeURIComponent(path)}` +
        `&name=${encodeURIComponent(name)}${replace ? "&replace=1" : ""}`;
    const answer = await fetch(`/api/files/upload?${query}`, {
        method: "POST",
        body: file,
        headers: { "X-Fellowship-Token": token },
    });
    if (!answer.ok) {
        const told = await answer.json().catch(() => ({ error: "" }));
        throw new FnasError(told.error || `The upload was refused (${answer.status}).`);
    }
    return (await answer.json()).size as number;
}

/** Where the browser asks for a file's bytes. Inline previews and downloads use
    the same answer; save asks for it as a download rather than showing it. */
export function fileUrl(share: string, path: string, save = false): string {
    const query = `share=${encodeURIComponent(share)}&path=${encodeURIComponent(path)}`;
    return `/api/files/download?${query}${save ? "&save=1" : ""}`;
}

/** Several things in one folder as a zip, which the NAS makes while the browser saves it. */
export function zipUrl(share: string, folder: string, names: string[]): string {
    const query = [`share=${encodeURIComponent(share)}`, `path=${encodeURIComponent(folder)}`,
        ...names.map(name => `name=${encodeURIComponent(name)}`)];
    return `/api/files/zip?${query.join("&")}`;
}

export function diskName(disk: { by_id: string | null; path: string }): string {
    return (disk.by_id || disk.path).replace("/dev/disk/by-id/", "");
}

export interface SystemInfo {
    hostname: string;
    timezone: string;
    time: number;
    ntp: boolean;
    uptime: number;
    certificate: {
        subject: string;
        issuer: string;
        expires: number;
        fingerprint: string;
        self_signed: boolean;
    } | null;
}
