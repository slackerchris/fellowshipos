export interface Disk {
    path: string;
    size: number;
    model: string;
    serial: string;
    bus: string;
    contents: string[] | null;
    refused?: string;
    layout?: Layout;
}

export interface Layout {
    disk: number;
    efi: number;
    system: number;
    reserve: number;
    swap: number;
    data: number;
}

export interface Machine {
    disks: Disk[];
    timezones: string[];
    installed: number;
    memory: number;
    min_system: number;
    split_from: number;
    // Which image this is, and what it calls itself
    edition: "nas" | "multi";
    title: string;
    subtitle: string;
    // True when the system takes the whole disk and no data part is offered
    whole_disk: boolean;
}

export interface Hello {
    allowed: boolean;
    local: boolean;
    code?: string;
    addresses?: string[];
    remote_urls?: string[];
}

export interface Progress {
    phase: "choose" | "installing" | "done" | "failed";
    steps: string[];
    step: number;
    log: string[] | null;
    error?: string;
}

export interface Settings {
    disk: string;
    system: number;
    hostname: string;
    timezone: string;
    user: string;
    full_name: string;
    password: string;
}

async function call<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
    let response: Response;
    try {
        response = await fetch(path, {
            method,
            credentials: "same-origin",
            headers: method === "POST" ? { "Content-Type": "application/json", "X-Fellowship-Installer": "1" } : {},
            body: body === undefined ? undefined : JSON.stringify(body),
        });
    } catch {
        throw new Error("The installer isn't answering. If the machine restarted, this page is no longer needed.");
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(data.error || `${response.status} ${response.statusText}`);
    }
    return data as T;
}

export const hello = () => call<Hello>("GET", "/api/hello");
export const pair = (code: string) => call<Hello>("POST", "/api/pair", { code });
export const machine = () => call<Machine>("GET", "/api/machine");
export const layoutFor = (size: number, system: number) => call<Layout>("POST", "/api/layout", { size, system });
export const install = (settings: Settings, confirm: string) => call<unknown>("POST", "/api/install", { ...settings, confirm });
export const progress = () => call<Progress>("GET", "/api/progress");
export const reboot = () => call<unknown>("POST", "/api/reboot");

export function size(bytes: number): string {
    const units = ["B", "KB", "MB", "GB", "TB", "PB"];
    let value = bytes;
    let unit = 0;
    while (value >= 1000 && unit < units.length - 1) {
        value /= 1000;
        unit++;
    }
    return `${value.toFixed(value < 10 && unit ? 1 : 0)} ${units[unit]}`;
}

export const GiB = 2 ** 30;

export function shortName(path: string) {
    return path.replace("/dev/", "");
}
