import { FunctionComponent } from "preact";
import { Alerts } from "../alerts";
import { Health } from "../health";
import { Settings } from "../settings";
import { Setup } from "../setup";
import { Shares } from "../shares";
import { Storage } from "../storage";
import { AppIconName } from "./icons";

export interface App {
    title: string;
    blurb: string;
    // Only nas-admins see it; the server refuses their requests for anyone else anyway
    admin: boolean;
    width: number;
    height: number;
    body: FunctionComponent;
}

export const APPS: Record<AppIconName, App> = {
    setup: { title: "Setup", blurb: "Pools, shares and alerts, step by step", admin: true, width: 820, height: 640, body: Setup },
    storage: { title: "Storage", blurb: "Pools and disks", admin: true, width: 900, height: 640, body: Storage },
    shares: { title: "Shares", blurb: "Folders on the network", admin: true, width: 940, height: 640, body: Shares },
    health: { title: "Health", blurb: "Disk health and problems", admin: true, width: 860, height: 600, body: Health },
    alerts: { title: "Alerts", blurb: "Email, phone and history", admin: true, width: 760, height: 600, body: Alerts },
    settings: { title: "Settings", blurb: "Your account, sessions and the NAS", admin: false, width: 820, height: 580, body: Settings },
};

export type AppId = AppIconName;

export function appsFor(admin: boolean): AppId[] {
    return (Object.keys(APPS) as AppId[]).filter(id => admin || !APPS[id].admin);
}
