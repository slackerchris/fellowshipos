// The Files app: the shares a person can use down the left, what a folder holds
// in the middle, and whatever is picked on the right: one file's preview, or what
// can be done with several at once.
import { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { FileEntry, FileHit, FileShare, copyFile, deleteFile, fileShares, fileUrl, folderIn, folderTree, humanSize,
    makeFolder, renameFile, searchShare, uploadFile, zipUrl } from "./api";
import { Button, Confirm, Field, Loading, Notice, Page, Result, useAction, useLoad } from "./ui";

const IMAGE = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp", ".svg"];
const VIDEO = [".mp4", ".webm", ".m4v", ".mov", ".ogv"];
const AUDIO = [".mp3", ".m4a", ".ogg", ".flac", ".wav", ".opus"];
const TEXT = [".txt", ".md", ".log", ".json", ".csv", ".conf", ".ini", ".yml", ".yaml",
    ".sh", ".py", ".ts", ".js", ".html", ".css"];

type Kind = "folder" | "image" | "video" | "audio" | "text" | "pdf" | "file";
type Drawing = Kind | "back" | "up" | "again" | "list" | "grid" | "close";

function kindOf(entry: { name: string; dir: boolean }): Kind {
    if (entry.dir) {
        return "folder";
    }
    const lower = entry.name.toLowerCase();
    if (IMAGE.some(ext => lower.endsWith(ext))) return "image";
    if (VIDEO.some(ext => lower.endsWith(ext))) return "video";
    if (AUDIO.some(ext => lower.endsWith(ext))) return "audio";
    if (lower.endsWith(".pdf")) return "pdf";
    if (TEXT.some(ext => lower.endsWith(ext))) return "text";
    return "file";
}

const KIND_NAMES: Record<Kind, string> = {
    folder: "Folder", image: "Picture", video: "Video", audio: "Audio", text: "Text", pdf: "PDF", file: "File",
};

/** Why the signed-in person can't change something, in words, or "" when they can.
    Whether they can is the kernel's answer; the reason is read from the permissions. */
function whyNot(entry: FileEntry | undefined, what = "it"): string {
    if (!entry || entry.writable !== false || !entry.mode) return "";
    const [owner, group, others] = [entry.mode[1], entry.mode[4], entry.mode[7]].map(bit => bit === "w");
    if (entry.acl) return `An access list on ${what} decides who can change it, and it doesn't let you.`;
    if (!owner && !group && !others) return `Nobody can change ${what}: it's read only for everyone.`;
    if (group) return `The ${entry.group} group can change ${what}, and you aren't in it.`;
    return `Only ${what === "it" ? "its" : "the"} owner, ${entry.owner}, can change ${what}.`;
}

/** Which of the extra columns show. Off at first, so the list stays clean on a phone. */
type Columns = { kind: boolean; owner: boolean; mode: boolean };

function savedColumns(): Columns {
    try {
        const saved = JSON.parse(localStorage.getItem("files-columns") || "{}");
        return { kind: !!saved.kind, owner: !!saved.owner, mode: !!saved.mode };
    } catch {
        return { kind: false, owner: false, mode: false };
    }
}

/** The drawings a browser needs: one per kind of file, and the toolbar's own.
    Stroke only, so each theme's colours carry them. */
const DRAWINGS: Record<Drawing, ComponentChildren> = {
    folder: <path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h3.2l1.8 2h5A1.5 1.5 0 0 1 16 8.5v6A1.5 1.5 0 0 1 14.5 16h-10A1.5 1.5 0 0 1 3 14.5z" />,
    image: <>
        <rect x="3" y="4.5" width="14" height="11" rx="1.6" />
        <circle cx="7" cy="8.4" r="1.2" />
        <path d="M3.6 13.6l3.7-3.3 2.5 2.2 2.4-2.5 4.2 4.1" />
    </>,
    video: <>
        <rect x="3" y="5" width="14" height="10" rx="1.6" />
        <path d="M8.5 8.1v3.8l3.3-1.9z" />
    </>,
    audio: <>
        <path d="M7.2 13.8V6.4l5.6-1.1v7.4" />
        <circle cx="5.6" cy="14" r="1.6" />
        <circle cx="11.2" cy="12.9" r="1.6" />
    </>,
    text: <>
        <path d="M5 4.5h7l3 3V15a.5.5 0 0 1-.5.5h-9A.5.5 0 0 1 5 15z" />
        <path d="M12 4.5V8h3M7.4 10.4h5M7.4 12.9h3.4" />
    </>,
    pdf: <>
        <path d="M5 4.5h7l3 3V15a.5.5 0 0 1-.5.5h-9A.5.5 0 0 1 5 15z" />
        <path d="M12 4.5V8h3M7.2 13.5l1.6-2.1 1.4 1.4 1-1.2 1.3 1.9" />
    </>,
    file: <>
        <path d="M5 4.5h7l3 3V15a.5.5 0 0 1-.5.5h-9A.5.5 0 0 1 5 15z" />
        <path d="M12 4.5V8h3" />
    </>,
    back: <path d="M12 5.5L6.5 11l5.5 5.5M6.5 11H15" />,
    up: <path d="M5.5 12l5.5-5.5 5.5 5.5M11 6.5v9" />,
    again: <>
        <path d="M15 11a5 5 0 1 1-1.7-3.8" />
        <path d="M15.4 4.6V8h-3.2" />
    </>,
    list: <path d="M4 5.6h11M4 11h11M4 16.4h11" />,
    grid: <>
        <rect x="4" y="5" width="4.3" height="4.3" rx="0.8" />
        <rect x="11.4" y="5" width="4.3" height="4.3" rx="0.8" />
        <rect x="4" y="11.4" width="4.3" height="4.3" rx="0.8" />
        <rect x="11.4" y="11.4" width="4.3" height="4.3" rx="0.8" />
    </>,
    close: <path d="M6 6l8 8M14 6l-8 8" />,
};

function Icon({ of, size = 18 }: { of: Drawing; size?: number }) {
    return (
        <svg class="file-icon" width={size} height={size} viewBox="0 0 18 18" aria-hidden="true"
             fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            {DRAWINGS[of]}
        </svg>
    );
}

type Sort = { by: "name" | "size" | "modified"; down: boolean };

/** The path split into crumbs. The first is the share, since "top" means nothing
    to anyone reading it. */
function crumbs(share: string, path: string): { name: string; to: string }[] {
    const parts = path.split("/").filter(Boolean);
    const found = [{ name: share, to: "" }];
    parts.forEach((part, index) => found.push({ name: part, to: parts.slice(0, index + 1).join("/") }));
    return found;
}

function join(path: string, name: string): string {
    return path ? `${path}/${name}` : name;
}

function parentOf(path: string): string {
    const parts = path.split("/").filter(Boolean);
    parts.pop();
    return parts.join("/");
}

/** What Copy or Cut put aside, to paste in another folder of the same share. */
type Clip = { share: string; folder: string; names: string[]; cut: boolean };

export function Files() {
    const shares = useLoad(fileShares);
    const [share, setShare] = useState<FileShare | null>(null);
    const [view, setView] = useState<"list" | "grid">("list");
    // Kept here, above the folder being shown, so it lasts while the person goes to another folder
    const [clip, setClip] = useState<Clip | null>(null);
    const [columns, setColumnsNow] = useState<Columns>(savedColumns);
    const setColumns = (next: Columns) => {
        setColumnsNow(next);
        try { localStorage.setItem("files-columns", JSON.stringify(next)); } catch { /* kept for this visit only */ }
    };
    return (
        <Loading loaded={shares}>
            {() => (
                <Page fill>
                    {shares.data!.shares.length === 0 ? (
                        <Notice>Your account can't use any shares yet. An administrator gives a share to you, or
                            to a group you're in, in Shares.</Notice>
                    ) : (
                        <div class={`files view-${view}`}>
                            <nav class="files-side" aria-label="Shares">
                                <p class="files-side-title">Shares</p>
                                {shares.data!.shares.map(item => (
                                    <button key={item.share} type="button"
                                            class={`files-share ${share?.share === item.share ? "current" : ""}`}
                                            aria-current={share?.share === item.share}
                                            onClick={() => setShare(item)}>
                                        <Icon of="folder" />
                                        <span class="files-share-name">{item.share}</span>
                                        {item.level === "read" && <span class="files-lock">read only</span>}
                                    </button>
                                ))}
                            </nav>
                            <div class="files-body">
                                {share
                                    ? <Browser key={share.share} share={share} view={view} setView={setView}
                                               clip={clip} setClip={setClip} columns={columns} setColumns={setColumns} />
                                    : <p class="files-blank">Choose a share to look inside.</p>}
                            </div>
                        </div>
                    )}
                </Page>
            )}
        </Loading>
    );
}

type Doing = { kind: "mkdir" } | { kind: "rename"; entry: FileEntry } | { kind: "delete"; entries: FileEntry[] }
    | { kind: "move"; entries: FileEntry[] } | null;

/** How a click picks: on its own, added to or taken from the rest, or everything since the last click. */
type Picking = { shiftKey?: boolean; ctrlKey?: boolean; metaKey?: boolean };

const plural = (count: number, one: string, many = `${one}s`) => `${count} ${count === 1 ? one : many}`;

function Browser({ share, view, setView, clip, setClip, columns, setColumns }: {
    share: FileShare; view: "list" | "grid"; setView: (view: "list" | "grid") => void;
    clip: Clip | null; setClip: (clip: Clip | null) => void;
    columns: Columns; setColumns: (columns: Columns) => void;
}) {
    const [columnsOpen, setColumnsOpen] = useState(false);
    const [path, setPath] = useState("");
    const [before, setBefore] = useState<string[]>([]);
    // The names picked, and the one a Shift-click or Shift-arrow measures from
    const [picked, setPicked] = useState<Set<string>>(new Set());
    const [anchor, setAnchor] = useState<string | null>(null);
    const [lead, setLead] = useState<string | null>(null);
    const [said, setSaid] = useState("");
    // A search across the share shows in place of the folder until the person goes back
    const [found, setFound] = useState<{ query: string; hits: FileHit[]; stopped?: string } | null>(null);
    const [looking, setLooking] = useState(false);
    // A file opened from the search: picked once its folder has loaded
    const [toPick, setToPick] = useState<{ folder: string; name: string } | null>(null);
    const [sort, setSort] = useState<Sort>({ by: "name", down: false });
    const [filter, setFilter] = useState("");
    const [doing, setDoing] = useState<Doing>(null);
    const [menuFor, setMenuFor] = useState<string | null>(null);
    const [busy, setBusy] = useState("");
    const [trouble, setTrouble] = useState<string | null>(null);
    const folder = useLoad(() => folderIn(share.share, path), [path]);
    // A share the person may change can still hold a folder they can't: the folder's
    // own answer from the kernel decides what is offered here
    const self = folder.data?.folder.self;
    const changing = share.level === "write" && self?.writable !== false;
    const folderWhy = share.level === "write" ? whyNot(self, "this folder") : "";

    // The path the listing belongs to, which is what its names are joined to:
    // while another folder loads, the old listing is still on screen
    const here = folder.data?.path ?? path;
    const all = folder.data?.folder.entries || [];
    const shown = filter
        ? all.filter(entry => entry.name.toLowerCase().includes(filter.toLowerCase()))
        : all;
    const items = [...shown].sort((a, b) => {
        if (a.dir !== b.dir) return a.dir ? -1 : 1;
        const order = sort.by === "name"
            ? a.name.toLowerCase().localeCompare(b.name.toLowerCase())
            : sort.by === "size" ? a.size - b.size
                : Date.parse(a.modified) - Date.parse(b.modified);
        return sort.down ? -order : order;
    });
    // Only what is shown counts as picked, so a filter never hides something a bulk action would touch
    const pickedItems = items.filter(item => picked.has(item.name));
    const chosen = pickedItems.length === 1 ? pickedItems[0] : null;
    const folders = items.filter(item => item.dir).length;

    const forget = () => { setPicked(new Set()); setAnchor(null); setLead(null); };
    const only = (name: string) => { setPicked(new Set([name])); setAnchor(name); setLead(name); };
    const between = (from: string | null, to: string): string[] => {
        const start = items.findIndex(item => item.name === from);
        const end = items.findIndex(item => item.name === to);
        if (start < 0 || end < 0) return [to];
        return items.slice(Math.min(start, end), Math.max(start, end) + 1).map(item => item.name);
    };
    /** A click: on its own picks one; Ctrl or Cmd adds or takes away; Shift picks the run from the last click. */
    const pick = (entry: FileEntry, how: Picking = {}) => {
        if (how.shiftKey && anchor) {
            const run = between(anchor, entry.name);
            setPicked(old => new Set(how.ctrlKey || how.metaKey ? [...old, ...run] : run));
            setLead(entry.name);
        } else if (how.ctrlKey || how.metaKey) {
            setPicked(old => {
                const next = new Set(old);
                if (next.has(entry.name)) next.delete(entry.name); else next.add(entry.name);
                return next;
            });
            setAnchor(entry.name);
            setLead(entry.name);
        } else {
            only(entry.name);
        }
    };

    const go = (to: string) => {
        setBefore(old => [...old, path]);
        setPath(to);
        forget();
        setSaid("");
        setFound(null);
    };
    useEffect(() => {
        if (toPick && folder.data && folder.data.path === toPick.folder) {
            only(toPick.name);
            setToPick(null);
        }
    }, [folder.data, toPick]);
    const look = async (query: string) => {
        setLooking(true);
        setTrouble(null);
        try {
            const answer = await searchShare(share.share, query);
            setFound({ query, hits: answer.hits, stopped: answer.stopped });
        } catch (error) {
            setTrouble((error as Error).message);
        }
        setLooking(false);
    };
    const openHit = (hit: FileHit) => {
        // The words searched for would only hide the rest of the folder it opens
        setFilter("");
        if (hit.dir) {
            go(hit.path);
        } else {
            go(parentOf(hit.path));
            setToPick({ folder: parentOf(hit.path), name: hit.name });
        }
    };
    const up = () => go(parentOf(path));
    const leaveSearch = () => {
        setFound(null);
        setFilter("");
    };
    const back = () => {
        const [last, ...rest] = [...before].reverse();
        if (last === undefined) return;
        setBefore(rest.reverse());
        setPath(last);
        forget();
        setSaid("");
    };
    const open = (entry: FileEntry) => {
        if (entry.dir) {
            go(join(here, entry.name));
        } else {
            only(entry.name);
        }
    };
    const again = () => { folder.reload(); forget(); };
    const newFolder = async (name: string) => {
        try {
            await makeFolder(share.share, here, name);
            setDoing(null);
            again();
        } catch (error) {
            setTrouble((error as Error).message);
        }
    };
    const newName = async (entry: FileEntry, name: string) => {
        try {
            await renameFile(share.share, join(here, entry.name), join(here, name));
            setDoing(null);
            again();
        } catch (error) {
            setTrouble((error as Error).message);
        }
    };
    // Each goes on its own, so one that is refused doesn't stop the rest, and the
    // status line says what happened to all of them
    const removeNow = async (entries: FileEntry[]) => {
        if (entries.length === 1) {
            try {
                await deleteFile(share.share, join(here, entries[0].name), entries[0].dir);
                setDoing(null);
                again();
            } catch (error) {
                setTrouble((error as Error).message);
            }
            return;
        }
        setDoing(null);
        setTrouble(null);
        const refused: string[] = [];
        for (const [at, entry] of entries.entries()) {
            setBusy(`Deleting ${entry.name} (${at + 1} of ${entries.length})`);
            try {
                await deleteFile(share.share, join(here, entry.name), entry.dir);
            } catch (error) {
                refused.push(`${entry.name}: ${(error as Error).message}`);
            }
        }
        setBusy("");
        const gone = entries.length - refused.length;
        setSaid(`${gone} deleted`);
        if (refused.length) setTrouble(`${refused.length} refused: ${refused.join("; ")}`);
        again();
    };
    // Uploads go one at a time, so a slow file doesn't hide the others' progress
    const send = async (files: FileList | File[], replace = false) => {
        setTrouble(null);
        const list = [...files];
        for (const [at, file] of list.entries()) {
            setBusy(`Uploading ${file.name} (${at + 1} of ${list.length})`);
            try {
                await uploadFile(share.share, here, file.name, file, replace);
            } catch (error) {
                setTrouble(`${file.name}: ${(error as Error).message}`);
            }
        }
        setBusy("");
        again();
    };
    // Dragging something picked moves everything picked with it
    const moveInto = async (names: string[], folderName: string) => {
        const refused: string[] = [];
        for (const name of names.filter(one => one !== folderName)) {
            try {
                await renameFile(share.share, join(here, name), join(join(here, folderName), name));
            } catch (error) {
                refused.push(`${name}: ${(error as Error).message}`);
            }
        }
        setTrouble(refused.length ? refused.join("; ") : null);
        again();
    };
    const dragged = (entry: FileEntry) => picked.has(entry.name) ? [...picked] : [entry.name];

    // Copy and Cut set things aside; Paste brings them here, copying or moving them.
    // Within one share only, since a share is where a person's rights are decided
    const pasting = clip && clip.share === share.share ? clip : null;
    const setAside = (entries: FileEntry[], cut: boolean) => {
        if (!entries.length) return;
        setClip({ share: share.share, folder: here, names: entries.map(entry => entry.name), cut });
        setSaid(`${plural(entries.length, "item")} ${cut ? "cut" : "copied"}: go to a folder and paste`);
    };
    const paste = async () => {
        if (!pasting) return;
        setTrouble(null);
        const refused: string[] = [];
        let done = 0;
        for (const [at, name] of pasting.names.entries()) {
            // Cut and pasted where it already is: nothing to do
            if (pasting.cut && pasting.folder === here) continue;
            setBusy(`${pasting.cut ? "Moving" : "Copying"} ${name} (${at + 1} of ${pasting.names.length})`);
            try {
                if (pasting.cut) {
                    await renameFile(share.share, join(pasting.folder, name), join(here, name));
                } else {
                    await copyFile(share.share, join(pasting.folder, name), join(here, name));
                }
                done += 1;
            } catch (error) {
                refused.push(`${name}: ${(error as Error).message}`);
            }
        }
        setBusy("");
        setSaid(`${done} ${pasting.cut ? "moved" : "copied"} here`);
        if (refused.length) setTrouble(`${refused.length} refused: ${refused.join("; ")}`);
        // What was moved isn't where Cut found it any more
        if (pasting.cut) setClip(null);
        folder.reload();
    };
    const moveTo = async (entries: FileEntry[], destination: string) => {
        setDoing(null);
        setTrouble(null);
        const refused: string[] = [];
        let done = 0;
        for (const entry of entries) {
            try {
                await renameFile(share.share, join(here, entry.name), join(destination, entry.name));
                done += 1;
            } catch (error) {
                refused.push(`${entry.name}: ${(error as Error).message}`);
            }
        }
        setSaid(`${done} moved to ${destination || share.share}`);
        if (refused.length) setTrouble(`${refused.length} refused: ${refused.join("; ")}`);
        again();
    };
    const isCut = (entry: FileEntry) => !!pasting && pasting.cut && pasting.folder === here && pasting.names.includes(entry.name);
    const sortBy = (by: Sort["by"]) => setSort(old => ({ by, down: old.by === by ? !old.down : false }));
    const ariaSort = (by: Sort["by"]) =>
        sort.by === by ? (sort.down ? "descending" : "ascending") : "none" as const;

    // A browser's keyboard: arrows pick, Shift with them picks a run, Ctrl or Cmd
    // with A picks everything shown, Enter opens, Delete asks to delete what is
    // picked, Backspace goes up, and Escape forgets the pick
    const keys = (event: KeyboardEvent) => {
        const at = items.findIndex(item => item.name === lead);
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            const step = event.key === "ArrowDown" ? 1 : -1;
            const next = items[Math.min(items.length - 1, Math.max(0, at + step))];
            if (!next) return;
            if (event.shiftKey && anchor) {
                setPicked(new Set(between(anchor, next.name)));
                setLead(next.name);
            } else {
                only(next.name);
            }
        } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
            event.preventDefault();
            setPicked(new Set(items.map(item => item.name)));
        } else if ((event.ctrlKey || event.metaKey) && ["c", "x", "v"].includes(event.key.toLowerCase())) {
            event.preventDefault();
            const key = event.key.toLowerCase();
            if (key === "c") setAside(pickedItems, false);
            else if (key === "x" && changing) setAside(pickedItems, true);
            else if (key === "v" && changing) paste();
        } else if (event.key === "Enter" && chosen) {
            event.preventDefault();
            open(chosen);
        } else if (event.key === "Delete" && changing && pickedItems.length) {
            event.preventDefault();
            setTrouble(null);
            setDoing({ kind: "delete", entries: pickedItems });
        } else if (event.key === "Backspace" && path) {
            event.preventDefault();
            up();
        } else if (event.key === "Escape") {
            if (found) setFound(null); else forget();
        }
    };

    return (
        <>
            <div class="files-tools">
                <button type="button" class="tool" title="Back" aria-label="Back" disabled={before.length === 0}
                        onClick={back}><Icon of="back" /></button>
                <button type="button" class="tool" title="Up one folder" aria-label="Up one folder"
                        disabled={path === ""} onClick={up}><Icon of="up" /></button>
                <button type="button" class="tool" title="Refresh" aria-label="Refresh"
                        onClick={folder.reload}><Icon of="again" /></button>
                <div class="crumbs" aria-label="Folder">
                    {crumbs(share.share, here).map((crumb, index, all) => (
                        <span key={crumb.to || "share"}>
                            <button type="button" class={index === all.length - 1 ? "current" : ""}
                                    onClick={() => go(crumb.to)}>{crumb.name}</button>
                            {index < all.length - 1 && <span class="crumb-sep" aria-hidden="true">›</span>}
                        </span>
                    ))}
                </div>
                <input class="files-filter" type="search" placeholder="Filter or search…"
                       aria-label="Filter this folder, or press Enter to search the whole share" value={filter}
                       title="Typing filters this folder. Press Enter, with at least 3 letters, to search the whole share."
                       onInput={event => setFilter(event.currentTarget.value)}
                       onKeyDown={event => {
                           if (event.key === "Enter" && filter.trim().length >= 3) {
                               event.preventDefault();
                               look(filter.trim());
                           }
                       }} />
                {changing && pasting && (
                    <button type="button" class="tool-text" onClick={paste}
                            title={`${pasting.cut ? "Move" : "Copy"} ${pasting.names.join(", ")} here`}>
                        Paste {pasting.names.length}
                    </button>
                )}
                {changing && (
                    <button type="button" class="tool-text" onClick={() => setDoing({ kind: "mkdir" })}>
                        New folder
                    </button>
                )}
                {changing && (
                    <label class="tool-text upload">
                        Upload
                        <input type="file" multiple aria-label="Upload files"
                               onChange={event => {
                                   const files = event.currentTarget.files;
                                   if (files && files.length) send(files);
                                   event.currentTarget.value = "";
                               }} />
                    </label>
                )}
                <span class="menu-wrap">
                    <button type="button" class="tool-text" aria-haspopup="menu" aria-expanded={columnsOpen}
                            onClick={() => setColumnsOpen(!columnsOpen)}>Columns</button>
                    {columnsOpen && (
                        <span class="row-menu columns-menu" role="menu">
                            {([["kind", "Kind"], ["owner", "Owner"], ["mode", "Permissions"]] as const).map(([key, label]) => (
                                <label key={key} class="check">
                                    <input type="checkbox" checked={columns[key]}
                                           onChange={event => setColumns({ ...columns, [key]: event.currentTarget.checked })} />
                                    <span>{label}</span>
                                </label>
                            ))}
                        </span>
                    )}
                </span>
                <div class="segmented view-switch" role="group" aria-label="How to show the files">
                    <button type="button" class={view === "list" ? "current" : ""} aria-pressed={view === "list"}
                            title="List" aria-label="List" onClick={() => setView("list")}><Icon of="list" /></button>
                    <button type="button" class={view === "grid" ? "current" : ""} aria-pressed={view === "grid"}
                            title="Grid" aria-label="Grid" onClick={() => setView("grid")}><Icon of="grid" /></button>
                </div>
            </div>

            {doing && doing.kind === "mkdir" && (
                <NameForm title="New folder" label="Folder name" value="" busy={false}
                          trouble={trouble} onCancel={() => { setDoing(null); setTrouble(null); }}
                          onDone={name => newFolder(name)} />
            )}
            {doing && doing.kind === "rename" && (
                <NameForm title={`Rename ${doing.entry.name}`} label="New name" value={doing.entry.name}
                          busy={false} trouble={trouble}
                          onCancel={() => { setDoing(null); setTrouble(null); }}
                          onDone={name => newName(doing.entry, name)} />
            )}
            {doing && doing.kind === "move" && (
                <MovePicker share={share.share} here={here} entries={doing.entries}
                            onCancel={() => setDoing(null)} onMove={to => moveTo(doing.entries, to)} />
            )}
            {doing && doing.kind === "delete" && doing.entries.length === 1 && (
                <Confirm title={`Delete ${doing.entries[0].name}`} phrase={doing.entries[0].name}
                         busy={false} outcome={trouble ? { kind: "error", text: trouble } : null}
                         onCancel={() => { setDoing(null); setTrouble(null); }}
                         onConfirm={() => removeNow(doing.entries)}>
                    <p>{doing.entries[0].dir
                        ? `${doing.entries[0].name} and everything in it goes for good.`
                        : `${doing.entries[0].name} goes for good.`}</p>
                </Confirm>
            )}
            {doing && doing.kind === "delete" && doing.entries.length > 1 && (
                <Confirm title={`Delete ${doing.entries.length} items`}
                         phrase={`delete ${doing.entries.length} from ${share.share}`}
                         busy={false} outcome={null}
                         onCancel={() => { setDoing(null); setTrouble(null); }}
                         onConfirm={() => removeNow(doing.entries)}>
                    <p>These go for good{doing.entries.some(entry => entry.dir) ? ", folders with everything in them" : ""}:</p>
                    <ul class="files-going">
                        {doing.entries.slice(0, 8).map(entry => <li key={entry.name}>{entry.name}</li>)}
                        {doing.entries.length > 8 && <li>and {plural(doing.entries.length - 8, "more")}</li>}
                    </ul>
                </Confirm>
            )}
            <Loading loaded={folder}>
                {() => (
                    <div class="files-split">
                        <div class="files-pane" tabindex={0} onKeyDown={keys}
                             onDragOver={event => { if (changing) event.preventDefault(); }}
                             onDrop={event => {
                                 if (!changing) return;
                                 event.preventDefault();
                                 if (event.dataTransfer?.files.length) send(event.dataTransfer.files);
                             }}>
                            <div class="file-scroll">
                            {found || looking ? (
                                <SearchResults share={share.share} found={found} looking={looking}
                                               onOpen={openHit} onBack={leaveSearch} />
                            ) : items.length === 0 ? (
                                <p class="files-blank"><Icon of="folder" size={30} />This folder is empty.</p>
                            ) : view === "list" ? (
                                <table class="file-table">
                                    <thead>
                                        <tr>
                                            <th aria-sort={ariaSort("name")}>
                                                <button type="button" onClick={() => sortBy("name")}>
                                                    Name {sort.by === "name" && <span class="caret">{sort.down ? "▾" : "▴"}</span>}
                                                </button>
                                            </th>
                                            <th class="file-size" aria-sort={ariaSort("size")}>
                                                <button type="button" onClick={() => sortBy("size")}>
                                                    Size {sort.by === "size" && <span class="caret">{sort.down ? "▾" : "▴"}</span>}
                                                </button>
                                            </th>
                                            <th class="file-when" aria-sort={ariaSort("modified")}>
                                                <button type="button" onClick={() => sortBy("modified")}>
                                                    Changed {sort.by === "modified" && <span class="caret">{sort.down ? "▾" : "▴"}</span>}
                                                </button>
                                            </th>
                                            {columns.kind && <th class="file-extra">Kind</th>}
                                            {columns.owner && <th class="file-extra">Owner</th>}
                                            {columns.mode && <th class="file-extra">Permissions</th>}
                                            <th class="file-actions" aria-label="Actions"></th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {items.map(entry => (
                                            <tr key={entry.name}
                                                class={`file-row ${entry.dir ? "is-folder" : ""} ${picked.has(entry.name) ? "picked" : ""} ${isCut(entry) ? "is-cut" : ""}`}
                                                aria-selected={picked.has(entry.name)}
                                                draggable={changing}
                                                onDragStart={event => event.dataTransfer?.setData("text/plain",
                                                    JSON.stringify(dragged(entry)))}
                                                onDragOver={event => {
                                                    if (changing && entry.dir) event.preventDefault();
                                                }}
                                                onDrop={event => {
                                                    const from = draggedNames(event.dataTransfer?.getData("text/plain"));
                                                    if (!changing || !entry.dir || !from.length) return;
                                                    event.preventDefault();
                                                    event.stopPropagation();
                                                    moveInto(from.filter(name => all.some(one => one.name === name)), entry.name);
                                                }}
                                                onClick={event => pick(entry, event)}
                                                onDblClick={() => open(entry)}>
                                                <td class="file-name">
                                                    <Thumb share={share.share} path={join(here, entry.name)} entry={entry} />
                                                    <span>{entry.name}</span>
                                                </td>
                                                <td class="file-size">{entry.dir ? "" : humanSize(entry.size)}</td>
                                                <td class="file-when">{new Date(entry.modified).toLocaleDateString()}</td>
                                                {columns.kind && <td class="file-extra">{KIND_NAMES[kindOf(entry)]}</td>}
                                                {columns.owner && <td class="file-extra">{entry.owner}
                                                    {entry.group && <span class="muted"> · {entry.group}</span>}</td>}
                                                {columns.mode && <td class="file-extra mono small"
                                                    title={whyNot(entry) || "You can change this"}>{entry.mode}{entry.acl ? "+" : ""}</td>}
                                                <td class="file-actions">
                                                    <span class="menu-wrap">
                                                        <button type="button" class="kebab" aria-haspopup="menu"
                                                                aria-expanded={menuFor === entry.name}
                                                                aria-label={`More for ${entry.name}`}
                                                                onClick={() => setMenuFor(menuFor === entry.name ? null : entry.name)}>⋯</button>
                                                        {menuFor === entry.name && (
                                                            <span class="row-menu" role="menu">
                                                                {entry.dir ? (
                                                                    <a role="menuitem" class="row-menu-link"
                                                                       href={zipUrl(share.share, here, [entry.name])}>Download as zip</a>
                                                                ) : (
                                                                    <a role="menuitem" class="row-menu-link"
                                                                       href={fileUrl(share.share, join(here, entry.name), true)}
                                                                       download={entry.name}>Download</a>
                                                                )}
                                                                <button type="button" role="menuitem"
                                                                    onClick={() => { setMenuFor(null); setAside(picked.has(entry.name) ? pickedItems : [entry], false); }}>
                                                                    Copy
                                                                </button>
                                                                {changing && <button type="button" role="menuitem"
                                                                    onClick={() => { setMenuFor(null); setAside(picked.has(entry.name) ? pickedItems : [entry], true); }}>
                                                                    Cut
                                                                </button>}
                                                                {changing && <button type="button" role="menuitem"
                                                                    onClick={() => { setMenuFor(null); setTrouble(null);
                                                                        setDoing({ kind: "move", entries: picked.has(entry.name) ? pickedItems : [entry] }); }}>
                                                                    Move to…
                                                                </button>}
                                                                {changing && <button type="button" role="menuitem"
                                                                    onClick={() => { setMenuFor(null); setTrouble(null); setDoing({ kind: "rename", entry }); }}>
                                                                    Rename…
                                                                </button>}
                                                                {changing && <button type="button" role="menuitem" class="danger-text"
                                                                    onClick={() => { setMenuFor(null); setTrouble(null); setDoing({ kind: "delete", entries: [entry] }); }}>
                                                                    Delete…
                                                                </button>}
                                                            </span>
                                                        )}
                                                    </span>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            ) : (
                                <ul class="file-grid">
                                    {items.map(entry => (
                                        <li key={entry.name}>
                                            <button type="button"
                                                    class={`file-tile ${picked.has(entry.name) ? "picked" : ""} ${isCut(entry) ? "is-cut" : ""}`}
                                                    aria-pressed={picked.has(entry.name)}
                                                    onClick={event => pick(entry, event)}
                                                    onDblClick={() => open(entry)}>
                                                <Thumb share={share.share} path={join(here, entry.name)} entry={entry} large />
                                                <span class="file-tile-name">{entry.name}</span>
                                            </button>
                                        </li>
                                    ))}
                                </ul>
                            )}
                            </div>
                            <p class="files-status">
                                <span>
                                    {busy ? busy
                                        : looking ? `Searching ${share.share}…`
                                        : found ? `${plural(found.hits.length, "match", "matches")} in ${share.share}`
                                        : items.length === 0
                                            ? (filter ? `Nothing matches “${filter}”` : "Nothing here")
                                            : pickedItems.length > 1
                                                ? `${pickedItems.length} of ${plural(items.length, "item")} picked`
                                                : plural(items.length, "item")}
                                    {!busy && !found && pickedItems.length <= 1 && folders > 0 && `, ${plural(folders, "folder")}`}
                                    {!busy && said && ` · ${said}`}
                                </span>
                                <span>{trouble ? trouble : share.level === "read" ? "Read only"
                                    : folderWhy ? `You can't change things here. ${folderWhy}` : ""}</span>
                            </p>
                        </div>
                        {chosen && !chosen.dir && (
                            <Preview share={share} entry={chosen} path={join(here, chosen.name)}
                                     onClose={forget} />
                        )}
                        {pickedItems.length > 1 && (
                            <Picked share={share} folder={here} entries={pickedItems} changing={changing}
                                    onDelete={() => { setTrouble(null); setDoing({ kind: "delete", entries: pickedItems }); }}
                                    onCopy={() => setAside(pickedItems, false)} onCut={() => setAside(pickedItems, true)}
                                    onMove={() => { setTrouble(null); setDoing({ kind: "move", entries: pickedItems }); }}
                                    onClose={forget} />
                        )}
                    </div>
                )}
            </Loading>
        </>
    );
}

const STOPPED: Record<string, string> = {
    hits: "Showing the first 500. Search for something longer to narrow it down.",
    time: "The search stopped after 2 seconds, so there may be more. Search for something longer.",
    depth: "Folders more than 8 deep weren't searched.",
};

/** What a search across the share found, each with the folder it is in. */
function SearchResults({ share, found, looking, onOpen, onBack }: {
    share: string; found: { query: string; hits: FileHit[]; stopped?: string } | null; looking: boolean;
    onOpen: (hit: FileHit) => void; onBack: () => void;
}) {
    if (looking || !found) {
        return <p class="files-blank">Searching {share}…</p>;
    }
    return (
        <div class="search-results">
            <div class="search-head">
                <button type="button" class="tool-text" onClick={onBack}>← Back to the folder</button>
                <span>{plural(found.hits.length, "match", "matches")} for “{found.query}” in {share}</span>
            </div>
            {found.stopped && <p class="muted small search-note">{STOPPED[found.stopped]}</p>}
            {found.hits.length === 0 ? (
                <p class="files-blank">Nothing in {share} has “{found.query}” in its name.</p>
            ) : (
                <table class="file-table">
                    <thead><tr><th>Name</th><th>In</th><th class="file-size">Size</th><th class="file-when">Changed</th></tr></thead>
                    <tbody>
                        {found.hits.map(hit => (
                            <tr key={hit.path} class={`file-row ${hit.dir ? "is-folder" : ""}`}
                                onClick={() => onOpen(hit)} title={`Open ${hit.path}`}>
                                <td class="file-name"><Icon of={kindOf(hit)} /><span>{hit.name}</span></td>
                                <td class="muted small">{parentOf(hit.path) || share}</td>
                                <td class="file-size">{hit.dir ? "" : humanSize(hit.size)}</td>
                                <td class="file-when">{new Date(hit.modified).toLocaleDateString()}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            )}
        </div>
    );
}

/** The names a drag carries: everything picked when the dragged one was among them. */
function draggedNames(carried: string | undefined): string[] {
    if (!carried) return [];
    try {
        const names = JSON.parse(carried);
        return Array.isArray(names) ? names.filter(name => typeof name === "string") : [];
    } catch {
        return [carried];
    }
}

/** Several things picked: how many, how big, and what can be done with them all. */
function Picked({ share, folder, entries, changing, onDelete, onCopy, onCut, onMove, onClose }: {
    share: FileShare; folder: string; entries: FileEntry[]; changing: boolean;
    onDelete: () => void; onCopy: () => void; onCut: () => void; onMove: () => void; onClose: () => void;
}) {
    const folders = entries.filter(entry => entry.dir).length;
    const files = entries.length - folders;
    const size = entries.reduce((sum, entry) => sum + (entry.dir ? 0 : entry.size), 0);
    return (
        <aside class="files-preview" aria-label={`${entries.length} items picked`}>
            <div class="preview-head">
                <Icon of="file" />
                <strong>{entries.length} items picked</strong>
                <button type="button" class="tool" title="Let go of them" aria-label="Let go of them" onClick={onClose}>
                    <Icon of="close" size={14} />
                </button>
            </div>
            <div class="preview-body picked-body">
                <p>{[files && plural(files, "file"), folders && plural(folders, "folder")].filter(Boolean).join(" and ")}
                    {files > 0 && <>, {humanSize(size)}{folders > 0 ? " besides what the folders hold" : ""}</>}</p>
                <p class="muted small">Ctrl-click (Cmd on a Mac) adds or takes one away; Shift-click picks a run.</p>
            </div>
            <div class="preview-foot">
                <div class="row">
                    <Button onClick={onCopy}>Copy</Button>
                    {changing && <Button onClick={onCut}>Cut</Button>}
                    {changing && <Button onClick={onMove}>Move to…</Button>}
                </div>
                {changing && <Button onClick={onDelete}>Delete…</Button>}
                <a class="button button-primary" href={zipUrl(share.share, folder, entries.map(entry => entry.name))}>
                    Download as zip</a>
            </div>
        </aside>
    );
}

/** Where to move something, picked from the share's folders: a way to move that
    needs no dragging, which a phone can't do. The folder it is in now, and a folder
    being moved with anything inside it, can't be picked. */
function MovePicker({ share, here, entries, onCancel, onMove }: {
    share: string; here: string; entries: FileEntry[]; onCancel: () => void; onMove: (to: string) => void;
}) {
    const tree = useLoad(() => folderTree(share));
    const moving = entries.filter(entry => entry.dir).map(entry => join(here, entry.name));
    const blocked = (folder: string) => folder === here
        || moving.some(path => folder === path || folder.startsWith(`${path}/`));
    const [to, setTo] = useState<string | null>(null);
    return (
        <div class="files-form">
            <p><strong>Move {entries.length === 1 ? entries[0].name : plural(entries.length, "item")} to…</strong></p>
            <Loading loaded={tree}>
                {() => (
                    <div class="move-tree" role="listbox" aria-label="Folders">
                        {["", ...tree.data!.folders].map(folder => {
                            const depth = folder ? folder.split("/").length : 0;
                            const off = blocked(folder);
                            return (
                                <button key={folder || "top"} type="button" role="option" disabled={off}
                                        aria-selected={to === folder}
                                        class={`move-folder ${to === folder ? "current" : ""}`}
                                        style={{ paddingLeft: `${8 + depth * 18}px` }}
                                        onClick={() => setTo(folder)}>
                                    <Icon of="folder" />
                                    <span>{folder ? folder.split("/").pop() : share}</span>
                                    {folder === here && <span class="muted small"> (here now)</span>}
                                </button>
                            );
                        })}
                        {tree.data!.cut && <p class="muted small">Only the first {tree.data!.folders.length} folders are
                            listed. Move it a step at a time, or drag it, to reach one further in.</p>}
                    </div>
                )}
            </Loading>
            <div class="row row-end">
                <Button onClick={onCancel}>Cancel</Button>
                <Button kind="primary" disabled={to === null} onClick={() => to !== null && onMove(to)}>
                    Move here</Button>
            </div>
        </div>
    );
}

/** One name, typed: a new folder's, or what something should be called. */
function NameForm({ title, label, value, busy, trouble, onCancel, onDone }: {
    title: string; label: string; value: string; busy: boolean; trouble: string | null;
    onCancel: () => void; onDone: (name: string) => void;
}) {
    const [name, setName] = useState(value);
    const field = useRef<HTMLInputElement>(null);
    useEffect(() => { field.current?.focus(); field.current?.select(); }, []);
    return (
        <form class="files-form" onSubmit={event => { event.preventDefault(); if (name.trim()) onDone(name.trim()); }}>
            <Field label={label}>
                <input ref={field} value={name} onInput={event => setName(event.currentTarget.value)} />
            </Field>
            <Result outcome={trouble ? { kind: "error", text: trouble } : null} />
            <div class="row row-end">
                <Button onClick={onCancel}>Cancel</Button>
                <Button kind="primary" type="submit" busy={busy} disabled={!name.trim() || name === value}>
                    {title}
                </Button>
            </div>
        </form>
    );
}

/** A picture shows itself; anything else shows its kind. */
function Thumb({ share, path, entry, large = false }: {
    share: string; path: string; entry: FileEntry; large?: boolean;
}) {
    const kind = kindOf(entry);
    const size = large ? "big" : "";
    if (kind === "image") {
        return <img class={`thumb ${size}`} src={fileUrl(share, path)} alt="" loading="lazy" />;
    }
    return <span class={`thumb ${size} kind-${kind}`}><Icon of={kind} size={large ? 40 : 18} /></span>;
}

function Preview({ share, entry, path, onClose }: {
    share: FileShare; entry: FileEntry; path: string; onClose: () => void;
}) {
    const url = fileUrl(share.share, path);
    const kind = kindOf(entry);
    return (
        <aside class="files-preview" aria-label={`${entry.name}, preview`}>
            <div class="preview-head">
                <Icon of={kind} />
                <strong title={entry.name}>{entry.name}</strong>
                <button type="button" class="tool" title="Close" aria-label="Close the preview" onClick={onClose}>
                    <Icon of="close" size={14} />
                </button>
            </div>
            <div class="preview-body">
                {kind === "image" && <img src={url} alt={entry.name} />}
                {kind === "video" && <video src={url} controls preload="metadata" />}
                {kind === "audio" && <audio src={url} controls preload="metadata" />}
                {kind === "pdf" && <iframe src={url} title={entry.name} />}
                {kind === "text" && <TextPreview url={url} />}
                {kind === "file" && <p class="preview-none">
                    <Icon of="file" size={26} />No preview for this kind of file. Download it to look inside.
                </p>}
            </div>
            <div class="preview-foot">
                <span>{KIND_NAMES[kind]} · {humanSize(entry.size)}</span>
                <span>Changed {new Date(entry.modified).toLocaleString()}</span>
                {entry.owner && <span>Owner {entry.owner}, group {entry.group}</span>}
                {entry.mode && <span class="mono small">{entry.mode}{entry.acl ? " + access list" : ""}</span>}
                {entry.writable !== undefined && (
                    <span class={entry.writable ? "muted small" : "small"}>
                        {entry.writable ? "You can change it." : whyNot(entry)}</span>
                )}
                <a class="button button-primary" href={fileUrl(share.share, path, true)} download={entry.name}>Download</a>
            </div>
        </aside>
    );
}

/** A text file's first part, since a log can be bigger than the browser wants. */
function TextPreview({ url }: { url: string }) {
    const text = useLoad(async () => {
        const answer = await fetch(url);
        if (!answer.ok) {
            throw new Error(answer.status === 403 ? "You don't have permission to open that."
                : "That file can't be read right now.");
        }
        return (await answer.text()).slice(0, 200_000);
    });
    return (
        <Loading loaded={text}>
            {() => <pre class="file-text">{text.data}</pre>}
        </Loading>
    );
}
