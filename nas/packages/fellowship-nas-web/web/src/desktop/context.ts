// What an app's window can ask of the desktop around it
import { createContext } from "preact";
import type { AppId } from "./apps";

export interface DesktopActions {
    open: (app: AppId) => void;
    close: (app: AppId) => void;
    // Setup is shown once: after it is finished or skipped it leaves the desktop
    setupFinished: () => void;
}

export const DesktopContext = createContext<DesktopActions>({ open() {}, close() {}, setupFinished() {} });
