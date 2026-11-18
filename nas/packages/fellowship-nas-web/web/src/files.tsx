// The Files app: the shares a person can use down the left, each opening into
// its folders and its recycle bin, what a folder holds in the middle, and
// whatever is picked on the right: one file's preview, what can be done with
// several at once, or something's properties.
import { ComponentChildren } from "preact";
import { useContext, useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { EarlierVersion, FileEntry, FileHit, FileShare, copyFile, deleteFile, earlierVersions, fileShares,
    fileUrl, folderIn, folderTree, humanSize, makeFolder, putBack, renameFile, restoreVersion, searchShare,
    uploadFile, zipUrl } from "./api";
import { DesktopContext } from "./desktop/context";
import { SessionContext } from "./settings";
import { Button, Confirm, Field, Loading, Notice, Page, Result, useAction, useLoad } from "./ui";

const IMAGE = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp", ".svg"];
const VIDEO = [".mp4", ".webm", ".m4v", ".mov", ".ogv"];
const AUDIO = [".mp3", ".m4a", ".ogg", ".flac", ".wav", ".opus"];
const TEXT = [".txt", ".md", ".log", ".json", ".csv", ".conf", ".ini", ".yml", ".yaml",
    ".sh", ".py", ".ts", ".js", ".html", ".css"];

type Kind = "folder" | "image" | "video" | "audio" | "text" | "pdf" | "file";
type Drawing = Kind | "back" | "up" | "again" | "list" | "grid" | "close" | "bin" | "open" | "shut";

/** The recycle bin's folder at the top of a share, as SMB's bin names it. Each
    person's things are in a folder of their own inside it. */
const RECYCLE = ".recycle";

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
    if (group && entry.group === "file-share") {
        return `This share is for everyone, but you've been taken out of the file-share group. `
            + "An administrator can put you back in Users.";
    }
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
    bin: <>
        <path d="M4.5 6h11M8 6V4.6h4V6M6 6l.7 9.2a1 1 0 0 0 1 .9h4.6a1 1 0 0 0 1-.9L14 6" />
        <path d="M8.6 8.6v5M11.4 8.6v5" />
    </>,
    open: <path d="M6 7.5l4 4 4-4" />,
    shut: <path d="M7.5 6l4 4-4 4" />,
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

/** Nothing to show: what an administrator does about it, or who to ask */
function NoShares() {
    const { admin } = useContext(SessionContext);
    const desktop = useContext(DesktopContext);
    if (!admin) {
        return <Notice>Your account can't use any shares yet. An administrator gives a share to you, or
            to a group you're in, in Shares.</Notice>;
    }
    return (
        <div class="stack">
            <Notice>Files shows the shares your account can use, and none are yours yet. Shares live on a pool:
                make a pool in Storage, then a share in Shares that you, or nas-admins, may use.</Notice>
            <div class="row">
                <Button onClick={() => desktop.open("storage")}>Open Storage</Button>
                <Button onClick={() => desktop.open("shares")}>Open Shares</Button>
            </div>
        </div>
    );
}

export function Files() {
    const { user } = useContext(SessionContext);
    const shares = useLoad(fileShares);
    const [share, setShare] = useState<FileShare | null>(null);
    // With one share there is nothing to choose, so it opens straight away
    const shown = share ?? (shares.data?.shares.length === 1 ? shares.data.shares[0] : null);
    const [view, setView] = useState<"list" | "grid">("list");
    // Kept here, above the folder being shown, so it lasts while the person goes to another folder
    const [clip, setClip] = useState<Clip | null>(null);
    const [columns, setColumnsNow] = useState<Columns>(savedColumns);
    const setColumns = (next: Columns) => {
        setColumnsNow(next);
        try { localStorage.setItem("files-columns", JSON.stringify(next)); } catch { /* kept for this visit only */ }
    };
    // Where the side sends the browser, asked for afresh each time; the folder the
    // browser is showing, so the side can mark it; and which shares are opened out
    const [jump, setJump] = useState<{ path: string; at: number } | null>(null);
    const [where, setWhere] = useState("");
    const [opened, setOpened] = useState<Set<string>>(new Set());
    // Counted up when the browser changes something, so the side's folders are read
    // again, and when the side moves something, so the browser's folder is
    const [changed, setChanged] = useState(0);
    const [moved, setMoved] = useState(0);
    const [sideNote, setSideNote] = useState("");
    const goTo = (item: FileShare, path: string) => {
        setSideNote("");
        if (shown?.share !== item.share) {
            setShare(item);
            setWhere(path);
        }
        setJump({ path, at: Date.now() });
    };
    const flip = (name: string) => setOpened(old => {
        const next = new Set(old);
        if (next.has(name)) next.delete(name); else next.add(name);
        return next;
    });
    // Something dragged from the folder on show, dropped on a folder in the side
    const dropOn = async (item: FileShare, target: string, carried: string | undefined) => {
        const drag = fromDrag(carried);
        if (!drag || !drag.names.length) return;
        if (drag.share !== item.share) {
            setSideNote("Moving between shares isn't offered: download it and upload it to the other share.");
            return;
        }
        const refused: string[] = [];
        for (const name of drag.names) {
            const from = join(drag.folder, name);
            if (drag.folder === target || target === from || target.startsWith(`${from}/`)) continue;
            try {
                await renameFile(item.share, from, join(target, name));
            } catch (error) {
                refused.push(`${name}: ${(error as Error).message}`);
            }
        }
        setSideNote(refused.length ? refused.join("; ") : "");
        setMoved(count => count + 1);
        setChanged(count => count + 1);
    };
    return (
        <Loading loaded={shares}>
            {() => (
                <Page fill>
                    {shares.data!.shares.length === 0 ? <NoShares /> : (
                        <div class={`files view-${view}`}>
                            <nav class="files-side" aria-label="Shares and their folders">
                                <p class="files-side-title">Shares</p>
                                {shares.data!.shares.map(item => {
                                    const isShown = shown?.share === item.share;
                                    const open = opened.has(item.share) || (isShown && where !== "");
                                    const bin = `${RECYCLE}/${user}`;
                                    const inBin = isShown && (where === bin || where.startsWith(`${bin}/`));
                                    return (
                                        <div key={item.share} class="files-side-share">
                                            <div class="tree-line">
                                                <button type="button" class="tree-caret"
                                                        aria-label={`${open ? "Hide" : "Show"} the folders in ${item.share}`}
                                                        aria-expanded={open}
                                                        onClick={() => {
                                                            if (open && isShown && where !== "") {
                                                                // Folded away while a folder inside is shown: go to the top
                                                                goTo(item, "");
                                                                setOpened(old => { const next = new Set(old); next.delete(item.share); return next; });
                                                            } else {
                                                                flip(item.share);
                                                            }
                                                        }}>
                                                    <Icon of={open ? "open" : "shut"} size={14} />
                                                </button>
                                                <button type="button"
                                                        class={`files-share ${isShown && where === "" ? "current" : ""}`}
                                                        aria-current={isShown && where === ""}
                                                        onClick={() => goTo(item, "")}
                                                        onDragOver={event => { if (item.level === "write") event.preventDefault(); }}
                                                        onDrop={event => {
                                                            event.preventDefault();
                                                            dropOn(item, "", event.dataTransfer?.getData("text/plain"));
                                                        }}>
                                                    <Icon of="folder" />
                                                    <span class="files-share-name">{item.share}</span>
                                                    {item.level === "read" && <span class="files-lock">read only</span>}
                                                </button>
                                            </div>
                                            {open && (
                                                <FolderTree share={item} current={isShown ? where : null} changed={changed}
                                                            onGo={path => goTo(item, path)}
                                                            onDrop={(path, carried) => dropOn(item, path, carried)} />
                                            )}
                                            {open && item.recycle && item.level === "write" && (
                                                <button type="button" class={`files-share tree-folder bin ${inBin ? "current" : ""}`}
                                                        style={{ paddingLeft: "26px" }} aria-current={inBin}
                                                        title={`What you delete in ${item.share} stays here ${plural(item.recycle_days, "day")}`}
                                                        onClick={() => goTo(item, bin)}>
                                                    <Icon of="bin" />
                                                    <span class="files-share-name">Recycle bin</span>
                                                </button>
                                            )}
                                        </div>
                                    );
                                })}
                                {sideNote && <p class="tree-note" role="status">{sideNote}</p>}
                            </nav>
                            <div class="files-body">
                                {shown
                                    ? <Browser key={shown.share} share={shown} person={user} view={view} setView={setView}
                                               clip={clip} setClip={setClip} columns={columns} setColumns={setColumns}
                                               jump={jump} moved={moved} onWhere={setWhere}
                                               onChanged={() => setChanged(count => count + 1)} />
                                    : <p class="files-blank">Choose a share to look inside.</p>}
                            </div>
                        </div>
                    )}
                </Page>
            )}
        </Loading>
    );
}

/** A share's folders, in the side, opening out like a file browser's tree. The
    folders on the way to the one shown open by themselves. */
function FolderTree({ share, current, changed, onGo, onDrop }: {
    share: FileShare; current: string | null; changed: number;
    onGo: (path: string) => void; onDrop: (path: string, carried: string | undefined) => void;
}) {
    const tree = useLoad(() => folderTree(share.share), [share.share, changed]);
    const [open, setOpen] = useState<Set<string>>(new Set());
    useEffect(() => {
        if (!current) return;
        setOpen(old => {
            const next = new Set(old);
            const parts = current.split("/");
            for (let at = 1; at < parts.length; at++) next.add(parts.slice(0, at).join("/"));
            return next;
        });
    }, [current]);
    if (tree.error) return <p class="tree-note">{tree.error}</p>;
    if (!tree.data) return <p class="tree-note">Loading…</p>;
    const below = new Map<string, string[]>();
    for (const folder of tree.data.folders) {
        const parent = parentOf(folder);
        below.set(parent, [...(below.get(parent) || []), folder]);
    }
    const row = (folder: string, depth: number): ComponentChildren => {
        const inside = below.get(folder) || [];
        const isOpen = open.has(folder);
        return (
            <div key={folder} role="none">
                <div class="tree-line" style={{ paddingLeft: `${depth * 12}px` }}>
                    {inside.length ? (
                        <button type="button" class="tree-caret" aria-expanded={isOpen}
                                aria-label={`${isOpen ? "Hide" : "Show"} the folders in ${folder.split("/").pop()}`}
                                onClick={() => setOpen(old => {
                                    const next = new Set(old);
                                    if (next.has(folder)) next.delete(folder); else next.add(folder);
                                    return next;
                                })}>
                            <Icon of={isOpen ? "open" : "shut"} size={14} />
                        </button>
                    ) : <span class="tree-caret" aria-hidden="true" />}
                    <button type="button" role="treeitem" aria-selected={current === folder}
                            class={`files-share tree-folder ${current === folder ? "current" : ""}`}
                            title={folder}
                            onClick={() => onGo(folder)}
                            onDragOver={event => { if (share.level === "write") event.preventDefault(); }}
                            onDrop={event => {
                                event.preventDefault();
                                onDrop(folder, event.dataTransfer?.getData("text/plain"));
                            }}>
                        <Icon of="folder" />
                        <span class="files-share-name">{folder.split("/").pop()}</span>
                    </button>
                </div>
                {isOpen && inside.map(child => row(child, depth + 1))}
            </div>
        );
    };
    const top = below.get("") || [];
    return (
        <div class="files-tree" role="tree" aria-label={`Folders in ${share.share}`}>
            {top.length === 0 && <p class="tree-note">No folders yet</p>}
            {top.map(folder => row(folder, 1))}
            {tree.data.cut && <p class="tree-note">Only the first {tree.data.folders.length} folders are shown here.</p>}
        </div>
    );
}

type Doing = { kind: "mkdir" } | { kind: "rename"; entry: FileEntry } | { kind: "delete"; entries: FileEntry[] }
    | { kind: "move"; entries: FileEntry[] } | { kind: "versions"; entry: FileEntry }
    | { kind: "properties"; entry: FileEntry | null } | { kind: "empty" } | null;

/** One line of a menu: something to do, a link to follow, or a rule between groups. */
type Action = {
    label: string; run?: () => void; href?: string; download?: string;
    danger?: boolean; disabled?: boolean; title?: string; keys?: string;
} | "-";

/** How a click picks: on its own, added to or taken from the rest, or everything since the last click. */
type Picking = { shiftKey?: boolean; ctrlKey?: boolean; metaKey?: boolean };

const plural = (count: number, one: string, many = `${one}s`) => `${count} ${count === 1 ? one : many}`;

function Browser({ share, person, view, setView, clip, setClip, columns, setColumns, jump, moved, onWhere, onChanged }: {
    share: FileShare; person: string; view: "list" | "grid"; setView: (view: "list" | "grid") => void;
    clip: Clip | null; setClip: (clip: Clip | null) => void;
    columns: Columns; setColumns: (columns: Columns) => void;
    jump: { path: string; at: number } | null; moved: number;
    onWhere: (path: string) => void; onChanged: () => void;
}) {
    const [columnsOpen, setColumnsOpen] = useState(false);
    const [path, setPath] = useState(jump?.path ?? "");
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
    // A right-click menu, where it was asked for inside the browser, and for what:
    // something in the folder, or the folder itself
    const [menu, setMenu] = useState<{ x: number; y: number; entry: FileEntry | null } | null>(null);
    const uploader = useRef<HTMLInputElement>(null);
    const [busy, setBusy] = useState("");
    const [trouble, setTrouble] = useState<string | null>(null);
    // The person's own part of the share's recycle bin, where the share has one
    const binRoot = share.recycle ? `${RECYCLE}/${person}` : null;
    const folder = useLoad(async () => {
        try {
            return await folderIn(share.share, path);
        } catch (error) {
            // Nothing deleted here yet, so the bin's folder hasn't been made: an empty bin
            if (binRoot && path === binRoot && /any more/.test((error as Error).message)) {
                return { share: share.share, path, folder: { entries: [] as FileEntry[] } };
            }
            throw error;
        }
    }, [path]);
    // A share the person may change can still hold a folder they can't: the folder's
    // own answer from the kernel decides what is offered here
    const self = folder.data?.folder.self;
    const changing = share.level === "write" && self?.writable !== false;
    const folderWhy = share.level === "write" ? whyNot(self, "this folder") : "";

    // The path the listing belongs to, which is what its names are joined to:
    // while another folder loads, the old listing is still on screen
    const here = folder.data?.path ?? path;
    const inBin = !!binRoot && (here === binRoot || here.startsWith(`${binRoot}/`));
    // Making, moving and naming happen outside the bin; in it, things are put back or deleted for good
    const adding = changing && !inBin;
    // The bin has its own place in the side, so its folder isn't listed among the share's
    const all = (folder.data?.folder.entries || [])
        .filter(entry => !(here === "" && share.recycle && entry.name === RECYCLE));
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
        setMenu(null);
        setDoing(old => old && old.kind === "properties" ? null : old);
    };
    // The side asked for a folder
    useEffect(() => {
        if (jump && jump.path !== path) go(jump.path);
    }, [jump?.at]);
    useEffect(() => onWhere(here), [here]);
    // The side moved something, which may have been here
    useEffect(() => { if (moved) folder.reload(); }, [moved]);
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
    const top = inBin ? binRoot! : "";
    const up = () => { if (path !== top && path !== "") go(parentOf(path)); };
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
    const again = () => { folder.reload(); forget(); onChanged(); };
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
    const toBin = share.recycle && !inBin;
    const removeNow = async (entries: FileEntry[]) => {
        if (entries.length === 1) {
            try {
                await deleteFile(share.share, join(here, entries[0].name), entries[0].dir);
                setDoing(null);
                again();
                setSaid(toBin ? `${entries[0].name} moved to the recycle bin` : `${entries[0].name} deleted`);
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
        again();
        setSaid(toBin ? `${gone} moved to the recycle bin` : `${gone} deleted`);
        if (refused.length) setTrouble(`${refused.length} refused: ${refused.join("; ")}`);
    };
    const emptyBin = async () => {
        setDoing(null);
        setTrouble(null);
        setBusy("Emptying the recycle bin");
        try {
            await deleteFile(share.share, binRoot!, true);
            setSaid("The recycle bin is empty");
        } catch (error) {
            setTrouble((error as Error).message);
        }
        setBusy("");
        again();
    };
    // Each goes back to the path it had, beside anything that has the name now
    const putBackNow = async (entries: FileEntry[]) => {
        setTrouble(null);
        const refused: string[] = [];
        for (const [at, entry] of entries.entries()) {
            setBusy(`Putting back ${entry.name} (${at + 1} of ${entries.length})`);
            try {
                await putBack(share.share, join(here, entry.name));
            } catch (error) {
                refused.push(`${entry.name}: ${(error as Error).message}`);
            }
        }
        setBusy("");
        again();
        setSaid(`${entries.length - refused.length} put back`);
        if (refused.length) setTrouble(`${refused.length} refused: ${refused.join("; ")}`);
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
    const dragged = (entry: FileEntry): Drag => ({ share: share.share, folder: here,
        names: picked.has(entry.name) ? [...picked] : [entry.name] });

    // Copy and Cut set things aside; Paste brings them here, copying or moving them.
    // Within one share only, since a share is where a person's rights are decided
    const pasting = clip && clip.share === share.share ? clip : null;
    const setAside = (entries: FileEntry[], cut: boolean) => {
        if (!entries.length) return;
        setClip({ share: share.share, folder: here, names: entries.map(entry => entry.name), cut });
        setSaid(`${plural(entries.length, "item")} ${cut ? "cut" : "copied"}: go to a folder and paste`);
    };
    const paste = async (into = here) => {
        if (!pasting) return;
        setTrouble(null);
        const refused: string[] = [];
        let done = 0;
        for (const [at, name] of pasting.names.entries()) {
            // Cut and pasted where it already is: nothing to do
            if (pasting.cut && pasting.folder === into) continue;
            setBusy(`${pasting.cut ? "Moving" : "Copying"} ${name} (${at + 1} of ${pasting.names.length})`);
            try {
                if (pasting.cut) {
                    await renameFile(share.share, join(pasting.folder, name), join(into, name));
                } else {
                    await copyFile(share.share, join(pasting.folder, name), join(into, name));
                }
                done += 1;
            } catch (error) {
                refused.push(`${name}: ${(error as Error).message}`);
            }
        }
        setBusy("");
        setSaid(`${done} ${pasting.cut ? "moved" : "copied"} ${into === here ? "here" : `to ${into.split("/").pop()}`}`);
        if (refused.length) setTrouble(`${refused.length} refused: ${refused.join("; ")}`);
        // What was moved isn't where Cut found it any more
        if (pasting.cut) setClip(null);
        folder.reload();
        onChanged();
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
    // What can be done with something, or with the folder itself, as the ⋯ menu
    // and the right-click menu both list it
    const actionsFor = (entry: FileEntry | null): Action[] => {
        if (!entry) {
            if (inBin) {
                return [
                    { label: "Empty recycle bin…", danger: true, disabled: !changing || all.length === 0,
                      run: () => { setTrouble(null); setDoing({ kind: "empty" }); } },
                    "-",
                    { label: "Select all", keys: "Ctrl+A", run: () => setPicked(new Set(items.map(item => item.name))) },
                    { label: "Refresh", run: again },
                ];
            }
            return [
                ...(adding ? [
                    { label: "New folder…", run: () => { setTrouble(null); setDoing({ kind: "mkdir" }); } },
                    { label: "Upload files…", run: () => uploader.current?.click() },
                ] : []),
                ...(adding && pasting ? [{ label: `Paste ${plural(pasting.names.length, "item")}`, keys: "Ctrl+V",
                    run: () => paste() }] : []),
                "-",
                { label: "Select all", keys: "Ctrl+A", run: () => setPicked(new Set(items.map(item => item.name))) },
                { label: "Refresh", run: again },
                "-",
                { label: "Properties", keys: "Alt+Enter", run: () => setDoing({ kind: "properties", entry: null }) },
            ];
        }
        const targets = picked.has(entry.name) && pickedItems.length ? pickedItems : [entry];
        const many = targets.length > 1;
        const download: Action = many
            ? { label: `Download ${targets.length} as zip`, href: zipUrl(share.share, here, targets.map(one => one.name)) }
            : entry.dir ? { label: "Download as zip", href: zipUrl(share.share, here, [entry.name]) }
                : { label: "Download", href: fileUrl(share.share, join(here, entry.name), true), download: entry.name };
        const properties: Action[] = many ? [] : [{ label: "Properties", keys: "Alt+Enter",
            run: () => setDoing({ kind: "properties", entry }) }];
        if (inBin) {
            return [
                ...(changing ? [{ label: many ? `Put back ${targets.length}` : "Put back", run: () => putBackNow(targets) }] : []),
                download,
                "-",
                ...(changing ? [{ label: "Delete for good…", keys: "Del", danger: true,
                    run: () => { setTrouble(null); setDoing({ kind: "delete", entries: targets }); } }] : []),
                "-",
                ...properties,
            ];
        }
        return [
            ...(many ? [] : [{ label: entry.dir ? "Open" : "Preview", keys: "Enter", run: () => open(entry) }]),
            download,
            "-",
            { label: "Copy", keys: "Ctrl+C", run: () => setAside(targets, false) },
            ...(adding ? [{ label: "Cut", keys: "Ctrl+X", run: () => setAside(targets, true) }] : []),
            ...(adding && pasting && entry.dir && !many ? [{ label: `Paste into ${entry.name}`,
                run: () => paste(join(here, entry.name)) }] : []),
            ...(adding && !many ? [{ label: "Rename…", keys: "F2",
                run: () => { setTrouble(null); setDoing({ kind: "rename", entry }); } }] : []),
            ...(adding ? [{ label: "Move to…", run: () => { setTrouble(null); setDoing({ kind: "move", entries: targets }); } }] : []),
            ...(!many && !entry.dir ? [{ label: "Earlier versions…", disabled: !share.snapshots,
                title: share.snapshots ? "" : "This pool doesn't support snapshots",
                run: () => { setTrouble(null); setDoing({ kind: "versions", entry }); } }] : []),
            "-",
            ...(adding ? [{ label: share.recycle ? "Move to recycle bin" : "Delete…", keys: "Del", danger: true,
                run: () => { setTrouble(null); setDoing({ kind: "delete", entries: targets }); } }] : []),
            "-",
            ...properties,
        ];
    };
    // A right-click: on something not already picked, it alone is picked, as in any
    // file browser; on the folder itself, nothing is
    const menuAt = (event: MouseEvent, entry: FileEntry | null) => {
        event.preventDefault();
        event.stopPropagation();
        if (entry && !picked.has(entry.name)) only(entry.name);
        if (!entry) forget();
        const body = (event.currentTarget as HTMLElement).closest(".files-body")?.getBoundingClientRect();
        setMenuFor(null);
        setMenu({ x: event.clientX - (body?.left ?? 0), y: event.clientY - (body?.top ?? 0), entry });
    };
    // The menu key, or Shift+F10, opens it beside what is picked
    const menuFromKeys = () => {
        const target = chosen ?? null;
        const row = target ? document.querySelector(`[data-file="${CSS.escape(target.name)}"]`) : null;
        const body = document.querySelector(".files-body")?.getBoundingClientRect();
        const box = row?.getBoundingClientRect();
        setMenu({ x: box ? box.left - (body?.left ?? 0) + 40 : 40, y: box ? box.bottom - (body?.top ?? 0) : 60, entry: target });
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
            else if (key === "x" && adding) setAside(pickedItems, true);
            else if (key === "v" && adding) paste();
        } else if (event.key === "Enter" && event.altKey) {
            event.preventDefault();
            setDoing({ kind: "properties", entry: chosen });
        } else if (event.key === "Enter" && chosen) {
            event.preventDefault();
            open(chosen);
        } else if (event.key === "F2" && chosen && adding) {
            event.preventDefault();
            setTrouble(null);
            setDoing({ kind: "rename", entry: chosen });
        } else if (event.key === "ContextMenu" || (event.key === "F10" && event.shiftKey)) {
            event.preventDefault();
            menuFromKeys();
        } else if (event.key === "Delete" && changing && pickedItems.length) {
            event.preventDefault();
            setTrouble(null);
            setDoing({ kind: "delete", entries: pickedItems });
        } else if (event.key === "Backspace" && path && path !== top) {
            event.preventDefault();
            up();
        } else if (event.key === "Escape") {
            if (menu) setMenu(null); else if (found) setFound(null); else forget();
        }
    };

    return (
        <>
            <div class="files-tools">
                <button type="button" class="tool" title="Back" aria-label="Back" disabled={before.length === 0}
                        onClick={back}><Icon of="back" /></button>
                <button type="button" class="tool" title="Up one folder" aria-label="Up one folder"
                        disabled={path === "" || path === top} onClick={up}><Icon of="up" /></button>
                <button type="button" class="tool" title="Refresh" aria-label="Refresh"
                        onClick={folder.reload}><Icon of="again" /></button>
                <div class="crumbs" aria-label="Folder">
                    {(inBin ? binCrumbs(binRoot!, here) : crumbs(share.share, here)).map((crumb, index, all) => (
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
                {adding && pasting && (
                    <button type="button" class="tool-text" onClick={() => paste()}
                            title={`${pasting.cut ? "Move" : "Copy"} ${pasting.names.join(", ")} here`}>
                        Paste {pasting.names.length}
                    </button>
                )}
                {adding && (
                    <button type="button" class="tool-text" onClick={() => setDoing({ kind: "mkdir" })}>
                        New folder
                    </button>
                )}
                {inBin && changing && (
                    <button type="button" class="tool-text danger-text" disabled={all.length === 0}
                            onClick={() => { setTrouble(null); setDoing({ kind: "empty" }); }}>
                        Empty recycle bin
                    </button>
                )}
                {adding && (
                    <label class="tool-text upload">
                        Upload
                        <input ref={uploader} type="file" multiple aria-label="Upload files"
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
            {doing && doing.kind === "delete" && (
                <DeleteAsk entries={doing.entries} share={share} into={toBin ? "bin" : inBin ? "bin-forever" : "forever"}
                           trouble={trouble} onCancel={() => { setDoing(null); setTrouble(null); }}
                           onConfirm={() => removeNow(doing.entries)} />
            )}
            {doing && doing.kind === "empty" && (
                <Ask title="Empty the recycle bin?" yes="Empty recycle bin" danger
                     onCancel={() => setDoing(null)} onConfirm={emptyBin}>
                    <p>Everything you deleted in {share.share} goes for good: {plural(all.length, "item")} here,
                        with whatever the folders hold. It can't be put back afterwards.</p>
                    <p class="muted small">Other people's deleted things are in bins of their own, and aren't touched.</p>
                </Ask>
            )}
            <Loading loaded={folder}>
                {() => (
                    <div class="files-split">
                        <div class="files-pane" tabindex={0} onKeyDown={keys}
                             onContextMenu={event => menuAt(event, null)}
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
                                inBin && here === binRoot ? (
                                    <p class="files-blank"><Icon of="bin" size={30} />Your recycle bin in {share.share} is empty.
                                        <span class="muted small">What you delete here stays {plural(share.recycle_days, "day")},
                                            then goes for good.</span></p>
                                ) : <p class="files-blank"><Icon of="folder" size={30} />This folder is empty.</p>
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
                                                data-file={entry.name}
                                                draggable={adding}
                                                onDragStart={event => event.dataTransfer?.setData("text/plain",
                                                    JSON.stringify(dragged(entry)))}
                                                onDragOver={event => {
                                                    if (adding && entry.dir) event.preventDefault();
                                                }}
                                                onContextMenu={event => menuAt(event, entry)}
                                                onDrop={event => {
                                                    const from = draggedNames(event.dataTransfer?.getData("text/plain"));
                                                    if (!adding || !entry.dir || !from.length) return;
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
                                                                <MenuItems items={actionsFor(entry)} onDone={() => setMenuFor(null)} />
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
                                                    data-file={entry.name}
                                                    onContextMenu={event => menuAt(event, entry)}
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
                                            : pickedItems.length ? pickedLine(pickedItems, items.length)
                                                : plural(items.length, "item")}
                                    {!busy && !found && pickedItems.length === 0 && folders > 0 && `, ${plural(folders, "folder")}`}
                                    {!busy && said && ` · ${said}`}
                                </span>
                                <span>{trouble ? trouble : share.level === "read" ? "Read only"
                                    : folderWhy ? `You can't change things here. ${folderWhy}`
                                    : inBin ? `Kept ${plural(share.recycle_days, "day")}, then gone for good` : ""}</span>
                            </p>
                        </div>
                        {doing && doing.kind === "versions" && (
                            <EarlierVersions share={share} entry={doing.entry} folder={here}
                                             changing={changing} onClose={() => { setDoing(null); setTrouble(null); }}
                                             onRestored={again} />
                        )}
                        {doing && doing.kind === "properties" && (
                            <Properties share={share} entry={doing.entry} folder={here} self={self}
                                        count={doing.entry ? null : items.length}
                                        binRoot={inBin ? binRoot : null}
                                        onClose={() => setDoing(null)} />
                        )}
                        {doing?.kind !== "properties" && chosen && !chosen.dir && (
                            <Preview share={share} entry={chosen} path={join(here, chosen.name)}
                                     onPutBack={inBin && changing ? () => putBackNow([chosen]) : undefined}
                                     onClose={forget} />
                        )}
                        {doing?.kind !== "properties" && pickedItems.length > 1 && (
                            <Picked share={share} folder={here} entries={pickedItems} changing={adding} inBin={inBin}
                                    onPutBack={() => putBackNow(pickedItems)}
                                    onDelete={() => { setTrouble(null); setDoing({ kind: "delete", entries: pickedItems }); }}
                                    onCopy={() => setAside(pickedItems, false)} onCut={() => setAside(pickedItems, true)}
                                    onMove={() => { setTrouble(null); setDoing({ kind: "move", entries: pickedItems }); }}
                                    onClose={forget} />
                        )}
                    </div>
                )}
            </Loading>
            {menu && (
                <ContextMenu x={menu.x} y={menu.y} label={menu.entry ? `For ${menu.entry.name}` : "For this folder"}
                             items={actionsFor(menu.entry)} onClose={() => setMenu(null)} />
            )}
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

/** What a drag carries: the share and folder it came from, and the names, which
    are everything picked when the dragged one was among them. */
type Drag = { share: string; folder: string; names: string[] };

function fromDrag(carried: string | undefined): Drag | null {
    if (!carried) return null;
    try {
        const drag = JSON.parse(carried);
        if (drag && typeof drag.share === "string" && typeof drag.folder === "string" && Array.isArray(drag.names)) {
            return { share: drag.share, folder: drag.folder,
                names: drag.names.filter((name: unknown) => typeof name === "string") };
        }
    } catch { /* not ours */ }
    return null;
}

/** The names a drag within the folder carries. */
function draggedNames(carried: string | undefined): string[] {
    return fromDrag(carried)?.names ?? [];
}

/** The crumbs inside the recycle bin, which starts at the bin rather than the share. */
function binCrumbs(binRoot: string, path: string): { name: string; to: string }[] {
    const parts = path.slice(binRoot.length).split("/").filter(Boolean);
    const found = [{ name: "Recycle bin", to: binRoot }];
    parts.forEach((part, index) => found.push({ name: part, to: [binRoot, ...parts.slice(0, index + 1)].join("/") }));
    return found;
}

/** What is picked, in the status line: one by name and size, several by count and size. */
function pickedLine(picked: FileEntry[], of: number): string {
    if (picked.length === 1) {
        const one = picked[0];
        return `${one.name} · ${one.dir ? "folder" : humanSize(one.size)}`;
    }
    const size = picked.reduce((sum, entry) => sum + (entry.dir ? 0 : entry.size), 0);
    const folders = picked.some(entry => entry.dir);
    return `${picked.length} of ${plural(of, "item")} picked · ${humanSize(size)}${folders ? " besides what the folders hold" : ""}`;
}

/** The lines of a menu, without a rule at either end or two together. */
function MenuItems({ items, onDone }: { items: Action[]; onDone: () => void }) {
    const tidy = items.filter((item, at) => item !== "-"
        || (at > 0 && at < items.length - 1 && items[at - 1] !== "-" && items.slice(at + 1).some(next => next !== "-")));
    while (tidy[0] === "-") tidy.shift();
    return (
        <>
            {tidy.map((item, at) => item === "-" ? <hr key={`rule-${at}`} class="menu-rule" /> : item.href ? (
                <a key={item.label} role="menuitem" class="row-menu-link" href={item.href} download={item.download}
                   onClick={onDone}>
                    <span>{item.label}</span>{item.keys && <span class="menu-keys">{item.keys}</span>}
                </a>
            ) : (
                <button key={item.label} type="button" role="menuitem" class={item.danger ? "danger-text" : ""}
                        disabled={item.disabled} title={item.title}
                        onClick={() => { onDone(); item.run?.(); }}>
                    <span>{item.label}</span>{item.keys && <span class="menu-keys">{item.keys}</span>}
                </button>
            ))}
        </>
    );
}

/** The right-click menu, where it was asked for, kept inside the window. It goes
    on Escape, a click anywhere else, or a choice. */
function ContextMenu({ x, y, label, items, onClose }: {
    x: number; y: number; label: string; items: Action[]; onClose: () => void;
}) {
    const box = useRef<HTMLDivElement>(null);
    const [at, setAt] = useState({ x, y });
    useLayoutEffect(() => {
        const menu = box.current;
        const room = menu?.parentElement?.getBoundingClientRect();
        if (!menu || !room) return;
        setAt({
            x: Math.max(4, Math.min(x, room.width - menu.offsetWidth - 4)),
            y: Math.max(4, Math.min(y, room.height - menu.offsetHeight - 4)),
        });
        (menu.querySelector("button:not([disabled]), a") as HTMLElement | null)?.focus();
    }, [x, y]);
    useEffect(() => {
        const away = (event: MouseEvent) => {
            if (box.current && !box.current.contains(event.target as Node)) onClose();
        };
        const keys = (event: KeyboardEvent) => {
            if (event.key === "Escape") { event.stopPropagation(); onClose(); }
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                const choices = [...(box.current?.querySelectorAll<HTMLElement>("button:not([disabled]), a") ?? [])];
                const now = choices.indexOf(document.activeElement as HTMLElement);
                const next = choices[(now + (event.key === "ArrowDown" ? 1 : choices.length - 1)) % choices.length];
                next?.focus();
            }
        };
        document.addEventListener("mousedown", away, true);
        document.addEventListener("keydown", keys, true);
        window.addEventListener("blur", onClose);
        return () => {
            document.removeEventListener("mousedown", away, true);
            document.removeEventListener("keydown", keys, true);
            window.removeEventListener("blur", onClose);
        };
    }, []);
    return (
        <div ref={box} class="row-menu context-menu" role="menu" aria-label={label}
             style={{ left: `${at.x}px`, top: `${at.y}px` }}
             onContextMenu={event => event.preventDefault()}>
            <MenuItems items={items} onDone={onClose} />
        </div>
    );
}

/** A plain question over the window: Cancel has focus, so a stray Enter is safe. */
function Ask({ title, yes, danger, children, trouble, onCancel, onConfirm }: {
    title: string; yes: string; danger?: boolean; children: ComponentChildren; trouble?: string | null;
    onCancel: () => void; onConfirm: () => void;
}) {
    const cancel = useRef<HTMLButtonElement>(null);
    useEffect(() => cancel.current?.focus(), []);
    return (
        <div class="overlay" role="dialog" aria-modal="true" aria-label={title}
             onKeyDown={event => { if (event.key === "Escape") onCancel(); }}>
            <form class="dialog" onSubmit={event => { event.preventDefault(); onConfirm(); }}>
                <h2>{title}</h2>
                {children}
                <Result outcome={trouble ? { kind: "error", text: trouble } : null} />
                <div class="row row-end">
                    <button ref={cancel} class="button button-plain" type="button" onClick={onCancel}>Cancel</button>
                    <Button kind={danger ? "danger" : "primary"} type="submit">{yes}</Button>
                </div>
            </form>
        </div>
    );
}

/** Asking before a delete, in words that say where things go: into the recycle
    bin for the share's number of days, or for good, and why for good. */
function DeleteAsk({ entries, share, into, trouble, onCancel, onConfirm }: {
    entries: FileEntry[]; share: FileShare; into: "bin" | "bin-forever" | "forever"; trouble: string | null;
    onCancel: () => void; onConfirm: () => void;
}) {
    const one = entries.length === 1 ? entries[0] : null;
    const what = one ? one.name : plural(entries.length, "item");
    const folders = entries.some(entry => entry.dir);
    const list = !one && (
        <ul class="files-going">
            {entries.slice(0, 8).map(entry => <li key={entry.name}>{entry.name}</li>)}
            {entries.length > 8 && <li>and {plural(entries.length - 8, "more")}</li>}
        </ul>
    );
    if (into === "bin") {
        return (
            <Ask title={`Move ${what} to the recycle bin?`} yes="Move to recycle bin" trouble={trouble}
                 onCancel={onCancel} onConfirm={onConfirm}>
                {list}
                <p>{one ? "It stays" : "They stay"} in your recycle bin in {share.share} for
                    {" "}{plural(share.recycle_days, "day")}{folders ? ", folders with everything in them" : ""}, and can be
                    put back from there until then.</p>
            </Ask>
        );
    }
    return (
        <Ask title={into === "bin-forever" ? `Delete ${what} for good?` : `Delete ${what}?`}
             yes={into === "bin-forever" ? "Delete for good" : "Delete"} danger trouble={trouble}
             onCancel={onCancel} onConfirm={onConfirm}>
            {list}
            <p>{one ? `${one.name}${one.dir ? " and everything in it" : ""} goes` : `These go${folders ? ", folders with everything in them," : ""}`} for
                good. {into === "bin-forever" ? "It can't be put back." : "It can't be undone."}</p>
            {into === "forever" && (
                <p class="muted small">{share.share} has no recycle bin. An administrator can turn one on in Shares,
                    in the share's SMB settings, and then Files keeps what is deleted for a while.</p>
            )}
        </Ask>
    );
}

/** Everything known about one thing, or the folder on show: what it is, where,
    how big, when it changed, whose it is and whether you may change it. */
function Properties({ share, entry, folder, self, count, binRoot, onClose }: {
    share: FileShare; entry: FileEntry | null; folder: string; self?: FileEntry; count: number | null;
    binRoot: string | null; onClose: () => void;
}) {
    const it = entry ?? self;
    const name = entry ? entry.name : folder ? folder.split("/").pop()! : share.share;
    const isFolder = entry ? entry.dir : true;
    const kind = entry ? kindOf(entry) : "folder";
    const path = entry ? join(folder, entry.name) : folder;
    const shownPath = (where: string) => binRoot ? where.slice(binRoot.length).replace(/^\//, "") : where;
    return (
        <aside class="files-preview" aria-label={`${name}, properties`}>
            <div class="preview-head">
                <Icon of={kind} />
                <strong title={name}>{name}</strong>
                <button type="button" class="tool" title="Close" aria-label="Close the properties" onClick={onClose}>
                    <Icon of="close" size={14} />
                </button>
            </div>
            <div class="preview-body properties-body">
                <dl class="properties">
                    <dt>Kind</dt><dd>{entry || folder ? KIND_NAMES[kind] : "Share"}</dd>
                    <dt>{binRoot ? "Was in" : "In"}</dt>
                    <dd>{binRoot
                        ? `${share.share}${shownPath(parentOf(path)) ? ` › ${shownPath(parentOf(path)).split("/").join(" › ")}` : ""}`
                        : entry || folder ? [share.share, ...parentOf(path).split("/").filter(Boolean)].join(" › ") : "This NAS"}</dd>
                    <dt>Size</dt>
                    <dd>{!isFolder && entry ? `${humanSize(entry.size)} (${entry.size.toLocaleString()} bytes)`
                        : count !== null ? `${plural(count, "item")} here; folders inside aren't added up`
                        : "Not added up for a folder. Open it to see what it holds."}</dd>
                    {it?.modified && <><dt>{binRoot ? "Deleted" : "Changed"}</dt><dd>{new Date(it.modified).toLocaleString()}</dd></>}
                    {it?.owner && <><dt>Owner</dt><dd>{it.owner}</dd></>}
                    {it?.group && <><dt>Group</dt><dd>{it.group}</dd></>}
                    {it?.mode && <><dt>Permissions</dt><dd class="mono">{it.mode}{it.acl ? " + access list" : ""}</dd></>}
                    <dt>You</dt>
                    <dd>{share.level === "read" ? "can look, not change: the share is read only for you"
                        : it?.writable === false ? whyNot(it, entry ? "it" : "this folder")
                        : it?.writable ? "can change it" : "—"}</dd>
                    {binRoot && <><dt>Recycle bin</dt><dd>Kept {plural(share.recycle_days, "day")} after it was
                        deleted, then gone for good</dd></>}
                </dl>
            </div>
            <div class="preview-foot">
                {entry && !entry.dir && <a class="button button-primary" href={fileUrl(share.share, path, true)}
                                           download={entry.name}>Download</a>}
                {entry && entry.dir && <a class="button" href={zipUrl(share.share, folder, [entry.name])}>Download as zip</a>}
            </div>
        </aside>
    );
}

/** Several things picked: how many, how big, and what can be done with them all. */
function Picked({ share, folder, entries, changing, inBin, onPutBack, onDelete, onCopy, onCut, onMove, onClose }: {
    share: FileShare; folder: string; entries: FileEntry[]; changing: boolean; inBin: boolean;
    onPutBack: () => void; onDelete: () => void; onCopy: () => void; onCut: () => void; onMove: () => void;
    onClose: () => void;
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
                {inBin ? (
                    <div class="row">
                        <Button kind="primary" onClick={onPutBack}>Put back</Button>
                        <Button onClick={onDelete}>Delete for good…</Button>
                    </div>
                ) : <>
                    <div class="row">
                        <Button onClick={onCopy}>Copy</Button>
                        {changing && <Button onClick={onCut}>Cut</Button>}
                        {changing && <Button onClick={onMove}>Move to…</Button>}
                    </div>
                    {changing && <Button onClick={onDelete}>{share.recycle ? "Move to recycle bin" : "Delete…"}</Button>}
                </>}
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

/** What one file looked like before: pick a version to look at, or bring a copy
    back beside it. Restoring writes a new file, so nothing is replaced. */
function EarlierVersions({ share, entry, folder, changing, onClose, onRestored }: {
    share: FileShare; entry: FileEntry; folder: string; changing: boolean;
    onClose: () => void; onRestored: () => void;
}) {
    const path = join(folder, entry.name);
    const versions = useLoad(() => earlierVersions(share.share, path), [path]);
    const [restoring, setRestoring] = useState<string | null>(null);
    const [trouble, setTrouble] = useState<string | null>(null);
    const [done, setDone] = useState<string | null>(null);
    const bringBack = async (version: EarlierVersion) => {
        setRestoring(version.name);
        setTrouble(null);
        setDone(null);
        try {
            const copy = copyName(entry.name, version.created);
            await restoreVersion(share.share, path, version.name, join(folder, copy));
            setDone(`Put back as ${copy}`);
            onRestored();
        } catch (error) {
            setTrouble((error as Error).message);
        } finally {
            setRestoring(null);
        }
    };
    return (
        <div class="files-versions" aria-label={`Earlier versions of ${entry.name}`}>
            <div class="preview-head">
                <Icon of="again" />
                <strong title={entry.name}>Earlier versions</strong>
                <button type="button" class="tool" title="Close" aria-label="Close earlier versions" onClick={onClose}>
                    <Icon of="close" size={14} />
                </button>
            </div>
            <div class="versions-body">
                <Loading loaded={versions}>
                    {() => versions.data!.versions.length === 0 ? (
                        <p class="muted small">No snapshot holds an earlier {entry.name}.</p>
                    ) : (
                        <ul class="version-list">
                            {versions.data!.versions.map(version => (
                                <li key={version.name}>
                                    <span class="version-when">{new Date(version.created * 1000).toLocaleString()}</span>
                                    <span class="small muted">{humanSize(version.size)}</span>
                                    <a class="link-button" href={fileUrl(share.share, path, false, version.name)}
                                       target="_blank" rel="noreferrer">Look</a>
                                    {changing && <Button busy={restoring === version.name}
                                                         onClick={() => bringBack(version)}>Put back a copy</Button>}
                                </li>
                            ))}
                        </ul>
                    )}
                </Loading>
                <Result outcome={trouble ? { kind: "error", text: trouble } : null} />
                <Result outcome={done ? { kind: "done", text: done } : null} />
            </div>
        </div>
    );
}

/** The name a restored copy is given, so it never lands on the file it came from. */
function copyName(name: string, created: number): string {
    const when = new Date(created * 1000).toLocaleDateString().replace(/\//g, "-");
    const dot = name.lastIndexOf(".");
    return dot > 0 ? `${name.slice(0, dot)} (from ${when})${name.slice(dot)}` : `${name} (from ${when})`;
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

function Preview({ share, entry, path, onPutBack, onClose }: {
    share: FileShare; entry: FileEntry; path: string; onPutBack?: () => void; onClose: () => void;
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
                <span>{onPutBack ? "Deleted" : "Changed"} {new Date(entry.modified).toLocaleString()}</span>
                {entry.owner && <span>Owner {entry.owner}, group {entry.group}</span>}
                {entry.mode && <span class="mono small">{entry.mode}{entry.acl ? " + access list" : ""}</span>}
                {entry.writable !== undefined && (
                    <span class={entry.writable ? "muted small" : "small"}>
                        {entry.writable ? "You can change it." : whyNot(entry)}</span>
                )}
                {onPutBack && <Button kind="primary" onClick={onPutBack}>Put back</Button>}
                <a class={`button ${onPutBack ? "" : "button-primary"}`} href={fileUrl(share.share, path, true)}
                   download={entry.name}>Download</a>
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
