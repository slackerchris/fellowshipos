// The Files app: the shares a person can use down the left, what a folder holds
// in the middle, and whatever is picked on the right. Uploading, moving and
// deleting come next, so a read-only share and one someone can write to look the
// same here.
import { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { FileEntry, FileShare, deleteFile, fileShares, fileUrl, folderIn, humanSize,
    makeFolder, renameFile, uploadFile } from "./api";
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

export function Files() {
    const shares = useLoad(fileShares);
    const [share, setShare] = useState<FileShare | null>(null);
    const [view, setView] = useState<"list" | "grid">("list");
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
                                    ? <Browser key={share.share} share={share} view={view} setView={setView} />
                                    : <p class="files-blank">Choose a share to look inside.</p>}
                            </div>
                        </div>
                    )}
                </Page>
            )}
        </Loading>
    );
}

type Doing = { kind: "mkdir" } | { kind: "rename"; entry: FileEntry } | { kind: "delete"; entry: FileEntry } | null;

function Browser({ share, view, setView }: {
    share: FileShare; view: "list" | "grid"; setView: (view: "list" | "grid") => void;
}) {
    const [path, setPath] = useState("");
    const [before, setBefore] = useState<string[]>([]);
    const [picked, setPicked] = useState<string | null>(null);
    const [sort, setSort] = useState<Sort>({ by: "name", down: false });
    const [filter, setFilter] = useState("");
    const [doing, setDoing] = useState<Doing>(null);
    const [menuFor, setMenuFor] = useState<string | null>(null);
    const [busy, setBusy] = useState("");
    const [trouble, setTrouble] = useState<string | null>(null);
    const folder = useLoad(() => folderIn(share.share, path), [path]);
    const changing = share.level === "write";

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
    const chosen = items.find(item => item.name === picked) || null;
    const folders = items.filter(item => item.dir).length;

    const go = (to: string) => {
        setBefore(old => [...old, path]);
        setPath(to);
        setPicked(null);
    };
    const up = () => go(parentOf(path));
    const back = () => {
        const [last, ...rest] = [...before].reverse();
        if (last === undefined) return;
        setBefore(rest.reverse());
        setPath(last);
        setPicked(null);
    };
    const open = (entry: FileEntry) => {
        if (entry.dir) {
            go(join(here, entry.name));
        } else {
            setPicked(entry.name);
        }
    };
    const again = () => { folder.reload(); setPicked(null); };
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
    const removeNow = async (entry: FileEntry) => {
        try {
            await deleteFile(share.share, join(here, entry.name), entry.dir);
            setDoing(null);
            again();
        } catch (error) {
            setTrouble((error as Error).message);
        }
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
    const moveInto = async (entry: FileEntry, folderName: string) => {
        try {
            await renameFile(share.share, join(here, entry.name), join(join(here, folderName), entry.name));
            again();
        } catch (error) {
            setTrouble((error as Error).message);
        }
    };
    const sortBy = (by: Sort["by"]) => setSort(old => ({ by, down: old.by === by ? !old.down : false }));
    const ariaSort = (by: Sort["by"]) =>
        sort.by === by ? (sort.down ? "descending" : "ascending") : "none" as const;

    // A browser's keyboard: arrows pick, Enter opens, Backspace goes up, Escape
    // forgets the pick
    const keys = (event: KeyboardEvent) => {
        const at = items.findIndex(item => item.name === picked);
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            const step = event.key === "ArrowDown" ? 1 : -1;
            const next = items[Math.min(items.length - 1, Math.max(0, at + step))];
            if (next) setPicked(next.name);
        } else if (event.key === "Enter" && chosen) {
            event.preventDefault();
            open(chosen);
        } else if (event.key === "Backspace" && path) {
            event.preventDefault();
            up();
        } else if (event.key === "Escape") {
            setPicked(null);
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
                <input class="files-filter" type="search" placeholder="Filter this folder"
                       aria-label="Filter this folder" value={filter}
                       onInput={event => setFilter(event.currentTarget.value)} />
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
            {doing && doing.kind === "delete" && (
                <Confirm title={`Delete ${doing.entry.name}`} phrase={doing.entry.name}
                         busy={false} outcome={trouble ? { kind: "error", text: trouble } : null}
                         onCancel={() => { setDoing(null); setTrouble(null); }}
                         onConfirm={() => removeNow(doing.entry)}>
                    <p>{doing.entry.dir
                        ? `${doing.entry.name} and everything in it goes for good.`
                        : `${doing.entry.name} goes for good.`}</p>
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
                            {items.length === 0 ? (
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
                                            <th class="file-actions" aria-label="Actions"></th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {items.map(entry => (
                                            <tr key={entry.name}
                                                class={`file-row ${entry.dir ? "is-folder" : ""} ${picked === entry.name ? "picked" : ""}`}
                                                aria-selected={picked === entry.name}
                                                draggable={changing}
                                                onDragStart={event => event.dataTransfer?.setData("text/plain", entry.name)}
                                                onDragOver={event => {
                                                    if (changing && entry.dir) event.preventDefault();
                                                }}
                                                onDrop={event => {
                                                    const from = event.dataTransfer?.getData("text/plain");
                                                    if (!changing || !entry.dir || !from || from === entry.name) return;
                                                    event.preventDefault();
                                                    event.stopPropagation();
                                                    const moved = all.find(one => one.name === from);
                                                    if (moved) moveInto(moved, entry.name);
                                                }}
                                                onClick={() => setPicked(entry.name)}
                                                onDblClick={() => open(entry)}>
                                                <td class="file-name">
                                                    <Thumb share={share.share} path={join(here, entry.name)} entry={entry} />
                                                    <span>{entry.name}</span>
                                                </td>
                                                <td class="file-size">{entry.dir ? "" : humanSize(entry.size)}</td>
                                                <td class="file-when">{new Date(entry.modified).toLocaleDateString()}</td>
                                                <td class="file-actions">
                                                    <span class="menu-wrap">
                                                        <button type="button" class="kebab" aria-haspopup="menu"
                                                                aria-expanded={menuFor === entry.name}
                                                                aria-label={`More for ${entry.name}`}
                                                                onClick={() => setMenuFor(menuFor === entry.name ? null : entry.name)}>⋯</button>
                                                        {menuFor === entry.name && (
                                                            <span class="row-menu" role="menu">
                                                                {!entry.dir && (
                                                                    <a role="menuitem" class="row-menu-link"
                                                                       href={fileUrl(share.share, join(here, entry.name), true)}
                                                                       download={entry.name}>Download</a>
                                                                )}
                                                                {changing && <button type="button" role="menuitem"
                                                                    onClick={() => { setMenuFor(null); setTrouble(null); setDoing({ kind: "rename", entry }); }}>
                                                                    Rename…
                                                                </button>}
                                                                {changing && <button type="button" role="menuitem" class="danger-text"
                                                                    onClick={() => { setMenuFor(null); setTrouble(null); setDoing({ kind: "delete", entry }); }}>
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
                                                    class={`file-tile ${picked === entry.name ? "picked" : ""}`}
                                                    onClick={() => setPicked(entry.name)}
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
                                        : items.length === 0
                                            ? (filter ? `Nothing matches “${filter}”` : "Nothing here")
                                            : `${items.length} item${items.length === 1 ? "" : "s"}`}
                                    {!busy && folders > 0 && `, ${folders} folder${folders === 1 ? "" : "s"}`}
                                </span>
                                <span>{trouble ? trouble : share.level === "read" ? "Read only" : ""}</span>
                            </p>
                        </div>
                        {chosen && !chosen.dir && (
                            <Preview share={share} entry={chosen} path={join(here, chosen.name)}
                                     onClose={() => setPicked(null)} />
                        )}
                    </div>
                )}
            </Loading>
        </>
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
                <span>{humanSize(entry.size)}</span>
                <span>Changed {new Date(entry.modified).toLocaleString()}</span>
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
