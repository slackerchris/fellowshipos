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
    layout: "single" | "mirror" | "raidz1" | "raidz2" | "raidz3";
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
    scan: (Progress & { function: "scrub" | "resilver"; errors: number }) | null;
    expand: Progress | null;
    expandable: boolean;
}

export interface Job {
    id: string;
    kind: "resilver" | "scrub" | "expand" | "backup";
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
    errors: number;
    space: {
        used: number;
        free: number;
        shares: { name: string; files: number; snapshots: number }[];
        parts: { shares: number; snapshots: number; backups: number; other: number };
    };
    history: { hourly: UsagePoint[]; daily: UsagePoint[] };
    projection: { days?: number; per_day?: number; reason?: string };
}

export interface Engine {
    name: "zfs" | "mergerfs" | "mdadm" | "btrfs";
    title: string;
    available: boolean;
    reason: string;
}

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
