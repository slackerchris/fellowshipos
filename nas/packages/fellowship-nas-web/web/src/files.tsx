// The Files app: browse the shares a person can use, look at what's in a folder,
// and open a file to see it. Uploading, moving and deleting come next, so a
// read-only share and a share someone can write to look the same here.
import { useState } from "preact/hooks";
import { FileEntry, FileShare, fileShares, fileUrl, folderIn, humanSize } from "./api";
import { Badge, Button, Card, Loading, Notice, Page, useLoad } from "./ui";

const IMAGE = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp", ".svg"];
const VIDEO = [".mp4", ".webm", ".m4v", ".mov", ".ogv"];
const AUDIO = [".mp3", ".m4a", ".ogg", ".flac", ".wav", ".opus"];
const TEXT = [".txt", ".md", ".log", ".json", ".csv", ".conf", ".ini", ".yml", ".yaml", ".sh", ".py", ".ts", ".js", ".html", ".css"];

type Kind = "image" | "video" | "audio" | "text" | "pdf" | "other";

function kindOf(name: string): Kind {
    const lower = name.toLowerCase();
    if (IMAGE.some(ext => lower.endsWith(ext))) return "image";
    if (VIDEO.some(ext => lower.endsWith(ext))) return "video";
    if (AUDIO.some(ext => lower.endsWith(ext))) return "audio";
    if (lower.endsWith(".pdf")) return "pdf";
    if (TEXT.some(ext => lower.endsWith(ext))) return "text";
    return "other";
}

// What a file's row shows in place of an icon
const MARK: Record<Kind, string> = { image: "▣", video: "▶", audio: "♪", text: "≡", pdf: "▤", other: "◻" };

/** The path split into the crumbs the desktop shows, and the path each one goes
    to when clicked. */
function crumbs(path: string): { name: string; to: string }[] {
    const parts = path.split("/").filter(Boolean);
    const found = [{ name: "top", to: "" }];
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
    return (
        <Loading loaded={shares}>
            {() => (
                <Page>
                    {shares.data!.shares.length === 0 ? (
                        <Notice>Your account can't use any shares yet. An administrator gives a share to you, or
                            to a group you're in, in Shares.</Notice>
                    ) : (
                        <div class="files">
                            <nav class="files-nav" aria-label="Shares">
                                {shares.data!.shares.map(item => (
                                    <button key={item.share} type="button"
                                            class={`files-share ${share?.share === item.share ? "current" : ""}`}
                                            aria-current={share?.share === item.share}
                                            onClick={() => setShare(item)}>
                                        <span class="files-share-name">{item.share}</span>
                                        <span class="small muted">{item.level === "write" ? "read and write" : "read only"}</span>
                                    </button>
                                ))}
                            </nav>
                            <div class="files-main">
                                {share
                                    ? <Browser key={share.share} share={share} />
                                    : <p class="muted">Choose a share to look inside.</p>}
                            </div>
                        </div>
                    )}
                </Page>
            )}
        </Loading>
    );
}

function Browser({ share }: { share: FileShare }) {
    const [path, setPath] = useState("");
    const [open, setOpen] = useState<FileEntry | null>(null);
    const folder = useLoad(() => folderIn(share.share, path));
    return (
        <>
            <div class="files-bar">
                <div class="crumbs" aria-label="Folder">
                    {crumbs(path).map((crumb, index, all) => (
                        <span key={crumb.to}>
                            <button type="button" class={index === all.length - 1 ? "current" : ""}
                                    onClick={() => { setPath(crumb.to); setOpen(null); }}>{crumb.name}</button>
                            {index < all.length - 1 && <span class="crumb-sep" aria-hidden="true">›</span>}
                        </span>
                    ))}
                </div>
                {path && <Button onClick={() => { setPath(parentOf(path)); setOpen(null); }}>Up</Button>}
            </div>
            <Loading loaded={folder}>
                {() => {
                    const entries = folder.data!.folder.entries;
                    return entries.length === 0 ? (
                        <p class="muted">This folder is empty.</p>
                    ) : (
                        <ul class="file-list">
                            {entries.map(entry => (
                                <li key={entry.name}>
                                    <button type="button" class={`file-row ${entry.dir ? "is-folder" : ""}`}
                                            onClick={() => entry.dir
                                                ? (setPath(join(path, entry.name)), setOpen(null))
                                                : setOpen(entry)}>
                                        <span class="file-mark" aria-hidden="true">
                                            {entry.dir ? "▸" : MARK[kindOf(entry.name)]}
                                        </span>
                                        <span class="file-name">{entry.name}</span>
                                        <span class="file-size small muted">{entry.dir ? "" : humanSize(entry.size)}</span>
                                        <span class="file-when small muted">{new Date(entry.modified).toLocaleDateString()}</span>
                                    </button>
                                </li>
                            ))}
                        </ul>
                    );
                }}
            </Loading>
            {open && <Preview share={share} entry={open} path={join(path, open.name)} onClose={() => setOpen(null)} />}
        </>
    );
}

function Preview({ share, entry, path, onClose }: {
    share: FileShare; entry: FileEntry; path: string; onClose: () => void;
}) {
    const url = fileUrl(share.share, path);
    const kind = kindOf(entry.name);
    return (
        <Card title={entry.name} actions={
            <>
                <a class="link-button" href={fileUrl(share.share, path, true)} download={entry.name}>Download</a>
                <Button onClick={onClose}>Close</Button>
            </>
        }>
            <div class="file-preview">
                {kind === "image" && <img src={url} alt={entry.name} />}
                {kind === "video" && <video src={url} controls preload="metadata" />}
                {kind === "audio" && <audio src={url} controls preload="metadata" />}
                {kind === "pdf" && <iframe src={url} title={entry.name} />}
                {kind === "text" && <TextPreview url={url} />}
                {kind === "other" && <p class="muted">
                    <Badge kind="neutral">No preview</Badge> This kind of file opens in its own app.
                    Download it to look inside.
                </p>}
            </div>
            <p class="small muted">{humanSize(entry.size)}, last changed {new Date(entry.modified).toLocaleString()}</p>
        </Card>
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
