// Long jobs (a resilver, scrub, RAIDZ expansion or backup) run on the NAS
// whether or not a browser watches. One stream of server-sent events keeps
// every window up to date while anyone is looking.
import { useEffect, useState } from "preact/hooks";
import { Job } from "./api";

type Listener = (jobs: Job[]) => void;

const listeners = new Set<Listener>();
let source: EventSource | null = null;
let latest: Job[] = [];
let retry: number | undefined;

function connect() {
    source = new EventSource("/api/jobs/events");
    source.addEventListener("jobs", event => {
        latest = JSON.parse((event as MessageEvent).data) as Job[];
        listeners.forEach(listener => listener(latest));
    });
    // The session ended or the helper went away: try again in a while, by
    // which time the page has logged in again or given up
    source.addEventListener("ended", () => {
        source?.close();
        source = null;
        retry = window.setTimeout(() => {
            retry = undefined;
            if (listeners.size) {
                connect();
            }
        }, 30_000);
    });
}

/** Every running and recent job, kept current while the component is shown. */
export function useJobs(): Job[] {
    const [jobs, setJobs] = useState<Job[]>(latest);
    useEffect(() => {
        listeners.add(setJobs);
        if (!source && retry === undefined) {
            connect();
        }
        return () => {
            listeners.delete(setJobs);
            if (!listeners.size) {
                source?.close();
                source = null;
                window.clearTimeout(retry);
                retry = undefined;
            }
        };
    }, []);
    return jobs;
}

/** Run something when a job finishes, such as reloading what it changed. */
export function useJobsFinished(after: () => void) {
    const jobs = useJobs();
    const running = jobs.filter(job => job.state === "running").map(job => job.id).join(",");
    const [seen, setSeen] = useState(running);
    useEffect(() => {
        if (running !== seen) {
            setSeen(running);
            after();
        }
    }, [running]);
    return jobs;
}
