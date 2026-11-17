// The NAS's clock, which the desktop shows instead of the device's: schedules,
// checks and alerts run on it, so the time beside them should be the same one.
// The NAS is asked once at sign-in and every few minutes after; between those
// the device's own clock ticks, corrected by how far apart the two were.
import { useEffect, useState } from "preact/hooks";
import { nasTime } from "./api";

export interface NasClock {
    // How far the NAS's clock is ahead of this device's, in milliseconds
    offset: number;
    zone: string;
    // Whether chrony has the NAS's clock synchronised, and whether the NAS could say
    synced: boolean;
    known: boolean;
    // False until the NAS answers, and while it can't: the device's own time is shown then
    read: boolean;
}

const deviceZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

let clock: NasClock = { offset: 0, zone: deviceZone(), synced: false, known: false, read: false };
const listeners = new Set<(clock: NasClock) => void>();
let timer = 0;

const ASK_EVERY = 5 * 60_000;

async function ask() {
    const sent = Date.now();
    try {
        const answer = await nasTime();
        // The NAS read its clock somewhere in the round trip; the middle is the best guess
        const offset = answer.now - (sent + Date.now()) / 2;
        let zone = answer.zone;
        try {
            new Intl.DateTimeFormat(undefined, { timeZone: zone });
        } catch {
            zone = "UTC";
        }
        clock = { offset, zone, synced: answer.synced, known: answer.known, read: true };
    } catch {
        clock = { ...clock, read: false };
    }
    listeners.forEach(listener => listener(clock));
}

// Asked only while a clock is on screen, so a signed-out page stops asking
function start() {
    if (!timer) {
        ask();
        timer = window.setInterval(ask, ASK_EVERY);
    }
}

function stop() {
    if (!listeners.size) {
        window.clearInterval(timer);
        timer = 0;
    }
}

/** The NAS's time, ticking every so often, with its zone and whether it can be trusted. */
export function useNasTime(every: number) {
    const [found, setFound] = useState(clock);
    const [, tick] = useState(0);
    useEffect(() => {
        listeners.add(setFound);
        start();
        setFound(clock);
        const ticking = window.setInterval(() => tick(n => n + 1), every);
        return () => {
            listeners.delete(setFound);
            window.clearInterval(ticking);
            stop();
        };
    }, [every]);
    return { now: new Date(Date.now() + found.offset), device: new Date(), deviceZone: deviceZone(), ...found };
}

/** A day's year, month and date as they are in a time zone, which can differ from the device's. */
export function dayIn(date: Date, zone: string) {
    const parts = new Intl.DateTimeFormat("en-US", { timeZone: zone, year: "numeric", month: "numeric", day: "numeric" })
        .formatToParts(date);
    const part = (type: string) => Number(parts.find(p => p.type === type)?.value);
    return new Date(part("year"), part("month") - 1, part("day"));
}
