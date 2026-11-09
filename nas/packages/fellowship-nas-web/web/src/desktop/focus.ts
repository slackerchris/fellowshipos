// Asking Health to show one disk's details, from a widget that names no window
import { useEffect, useState } from "preact/hooks";

let chosen: string | null = null;
const listeners = new Set<(path: string) => void>();

/** The disk a widget wants to see. The caller opens Health afterwards. */
export function focusDisk(path: string) {
    chosen = path;
    listeners.forEach(listener => listener(path));
}

/** The disk Health was last asked to show, and any later request while mounted. */
export function useDiskFocus(): string | null {
    const [path, setPath] = useState<string | null>(chosen);
    useEffect(() => {
        listeners.add(setPath);
        return () => { listeners.delete(setPath); };
    }, []);
    return path;
}
