// Every change goes through fnas, which checks its arguments and is the only
// thing nas-admins may run as root (see the polkit rule)

interface SpawnError {
    problem?: string;
    message?: string;
    exit_status?: number;
}

interface Process extends Promise<string> {
    input(data: string): Process;
}

interface Cockpit {
    spawn(args: string[], options?: { err?: "message" | "out" | "ignore"; superuser?: string }): Process;
    file(path: string): { read(): Promise<string | null> };
}

declare const cockpit: Cockpit;

export class FnasError extends Error {}

const NOT_ADMIN = "Your account can't change the NAS. Ask an administrator to add it to the nas-admins group, then log in again.";

function explain(error: SpawnError): string {
    const text = (error.message || error.problem || "fnas failed").trim();
    // pkexec reports a refused rule as a missing agent when nothing can ask for a password
    if (/Not authorized|dismissed|not-authorized|access-denied|authentication agent/i.test(text)) {
        return NOT_ADMIN;
    }
    return text.replace(/^fnas: /gm, "");
}

/** Run fnas and return what it printed. */
export function fnas(args: string[], input?: string): Promise<string> {
    return new Promise((resolve, reject) => {
        const process = cockpit.spawn(["pkexec", "/usr/sbin/fnas", ...args], { err: "message" });
        process.then(resolve, (error: SpawnError) => reject(new FnasError(explain(error))));
        if (input !== undefined) {
            process.input(input);
        }
    });
}

export async function fnasJson<T>(args: string[]): Promise<T> {
    return JSON.parse(await fnas(["--json", ...args])) as T;
}

/** People's accounts, read without needing root. Service accounts like nas-share can't log in. */
export async function localUsers(): Promise<string[]> {
    const passwd = (await cockpit.file("/etc/passwd").read()) || "";
    return passwd.split("\n")
        .map(line => line.split(":"))
        .filter(fields => Number(fields[2]) >= 1000 && Number(fields[2]) < 60000
                          && !/(nologin|false)$/.test(fields[6] || ""))
        .map(fields => fields[0]);
}

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
