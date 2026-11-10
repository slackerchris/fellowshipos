import { FunctionComponent } from "preact";
import { Alerts } from "../alerts";
import { Backups } from "../backups";
import { Files } from "../files";
import { Health } from "../health";
import { Settings } from "../settings";
import { Setup } from "../setup";
import { Shares } from "../shares";
import { Storage } from "../storage";
import { Users } from "../users";
import { AppIconName } from "./icons";

export interface App {
    title: string;
    // A shorter name under the desktop icon, where the title doesn't fit
    label?: string;
    blurb: string;
    // Only nas-admins see it; the server refuses their requests for anyone else anyway
    admin: boolean;
    width: number;
    height: number;
    body: FunctionComponent;
}

export const APPS: Record<AppIconName, App> = {
    // Everyone has Files: it shows the shares their own account can use
    files: { title: "Files", blurb: "Browse your shares, and open a file", admin: false, width: 900, height: 640, body: Files },
    setup: { title: "Setup", blurb: "Pools, shares and alerts, step by step", admin: true, width: 820, height: 640, body: Setup },
    storage: { title: "Storage", blurb: "Pools and disks", admin: true, width: 900, height: 640, body: Storage },
    users: { title: "Users", blurb: "People, their passwords, and groups", admin: true, width: 900, height: 640, body: Users },
    shares: { title: "Shares", blurb: "Folders on the network, and every way to reach them", admin: true, width: 940, height: 640, body: Shares },
    backups: { title: "Snapshots and backup", label: "Snapshots & backup", blurb: "Earlier versions, backup jobs and restores", admin: true, width: 900, height: 640, body: Backups },
    health: { title: "Health", blurb: "Every check, service, pool and disk", admin: true, width: 860, height: 600, body: Health },
    alerts: { title: "Alerts", blurb: "Notification history, email and ntfy", admin: true, width: 760, height: 600, body: Alerts },
    settings: { title: "Settings", blurb: "Your account, sessions and the NAS", admin: false, width: 820, height: 580, body: Settings },
};

export type AppId = AppIconName;

export function appsFor(admin: boolean): AppId[] {
    return (Object.keys(APPS) as AppId[]).filter(id => admin || !APPS[id].admin);
}

/** The apps with an icon: Setup only until it has been finished or skipped. */
export function iconsFor(admin: boolean, setupDone: boolean): AppId[] {
    return appsFor(admin).filter(id => id !== "setup" || !setupDone);
}
