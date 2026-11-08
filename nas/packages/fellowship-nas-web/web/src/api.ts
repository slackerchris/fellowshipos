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
}

export interface Pool {
    name: string;
    size: number;
    allocated: number;
    free: number;
    health: string;
    readonly: "on" | "off";
}

export interface ImportablePool {
    pool: string;
    id: string;
    state: string;
    status?: string;
    action?: string;
}

export type Policy = { hourly: number; daily: number; monthly: number };

export interface Share {
    name: string;
    pool: string;
    dataset: string;
    path: string;
    mode: "shared" | "matched";
    nfs: boolean;
    smb: boolean;
    policy: Policy;
    readonly: boolean;
}

export interface Problem {
    key: string;
    severity: "info" | "warning" | "critical";
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
    verdict: "healthy" | "watch" | "failing" | "unknown";
    notes: string[];
    temperature: number | null;
    hours: number | null;
}

export interface Health {
    problems: Problem[];
    disks: DiskHealth[];
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

export const LAYOUTS = {
    single: { disks: 1, text: "One disk, no redundancy" },
    mirror: { disks: 2, text: "Pairs of disks holding the same data; survives one failure per pair" },
    raidz1: { disks: 3, text: "Survives one disk failing" },
    raidz2: { disks: 4, text: "Survives two disks failing" },
} as const;

export type Layout = keyof typeof LAYOUTS;

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
