# FellowshipNAS: what Files still needs

A work plan for the rest of Phase 12, written November 10, 2026 against
`nas-edition` at `0e08f77`, and brought up to date at `f1f0228` with slices A to D
built and again with slice E built. It sits under [the plan](fellowship-nas-plan.md) and
[the brief](fellowship-nas-next-steps.md), and covers only the Files app and the
gate it carries; everything else stays where the plan puts it.

What is built, so the gaps are clear: browse, download and preview as the person;
upload, new folder, rename, move by dragging, delete, and a filter box; the path
resolver and its refusal tests; the helper running file work as the signed-in
person inside their share roots; and slices A to E below. The phone (F) is being
kept as its own project, and the gate (G) is walked on real hardware by Chris
rather than in the VM here.

## 1. Order, and why

Each slice can ship on its own, and each ends with something to look at in the
harness. The order puts the things a browser is judged by first, then the one
thing only ZFS can do, then the phone, because the phone work changes layouts and
is best done once the interactions have settled.

| | Slice | Why here |
| --- | --- | --- |
| A | Multi-select and bulk actions (built, `f0a14c7`) | The first thing anyone tries after moving one file |
| B | Copy and paste, and a "Move to…" picker (built, `9a010ec`) | Completes the operations, and gives touch a way to move without dragging |
| C | Search across the share (built, `baee7c8`) | The filter only sees one folder; searching is what people expect next |
| D | Kind, owner and permissions columns (built, `f1f0228`) | Answers "why can't I write this?" without a shell |
| E | Look at an earlier version and put one back as a copy (built) | The last gate clause that needs server work, ZFS only |
| F | The installable phone app (its own project) | Changes layout and input, so last |
| G | The gate, on real hardware | Proves the whole thing on a real NAS |

The Phase 10 and 11 catch-up tests in the brief's section 3 are still owed by
name and are not part of this plan. They gate the release, not this phase, but do
them before the release candidate or they will be forgotten.

## 2. Slice A: multi-select and bulk actions

**Built** (Nov 10), as below, with Move left to slice B: dragging a picked item onto a
folder moves everything picked with it, which covers the mouse. The zip's answer runs
to the end of the connection, since its size isn't known before it is made.

Built from the same list that exists: selection becomes a set of names rather
than one name.

- Click selects one; Ctrl-click (Cmd on a Mac) adds or removes one; Shift-click
  selects the range from the last click. Ctrl-A selects everything shown, Escape
  clears, and Shift with the arrow keys extends.
- The preview pane shows a single picked file; with several picked it says
  "4 items picked" and offers the actions instead.
- Bulk actions: **Delete** (typed phrase names the count and the share, as fnas
  does elsewhere), **Download** as a zip (server side, see below), and **Move**
  once slice B lands.
- Download of several files: `POST /api/files/zip` with the list, answered by a
  stream the child builds with `archive/zip` into a pipe. One zip of 4 GB is a
  long job, so cap the total at 2 GB and stream, rather than writing a temporary
  file into the share.
- Tests: the set survives a folder change by being cleared; a bulk delete of ten
  files runs as ten individual ops, so one refusal doesn't stop the rest, and the
  status line reports "9 deleted, 1 refused: …".

## 3. Slice B: copy and paste, and moving without a mouse

**Built** (Nov 10), as below. Pasting a copy onto a name that is there is refused, as
planned, so duplicating within one folder needs a rename first.

Dragging is a mouse gesture; the phone gate says "rename, move and delete by
tapping". Both need the same operation, so add a clipboard and a picker.

- **Clipboard in the browser only**, holding `{share, path, action: "copy"|"cut"}`.
  Copy and Cut in the row menu, Paste in the folder's own menu and on an empty
  area. Paste with `cut` is a rename; with `copy` it is a copy, which needs a new
  child op.
- **New child op `copy <from> <to>`**, files by `io.Copy` and folders by walking,
  written into a temporary name and renamed into place like an upload, so a copy
  that fails leaves nothing half-made. Refuse to copy a folder into itself.
- **"Move to…"** in the row menu opens a folder picker: a small tree of the
  share's folders from a new `file-child tree <path> <depth>` op, capped at 500
  folders and 8 deep, with the current folder greyed.
- Same share only, for now. Moving between shares is a copy then a delete, and
  belongs with a "Copy to…" that names a second share; leave both out until the
  first version is used.
- Tests: paste onto an existing name is refused with the same words as an
  upload; a cut onto the file's own name is a no-op; a copy of a folder into its
  own child is refused; a picker never offers a folder outside the share.

## 4. Slice C: search

**Built** (Nov 10), as below. It searches the whole share from its top rather than from
the folder being shown, which is what "in this share" promises.

The filter box stays for one folder. Searching needs the walk in the child.

- New op `file-search <root> <query> <limit>` running `filepath.WalkDir` as the
  person, matching the name case-insensitively, capped at 500 results and 8
  levels, stopping early. It answers a flat list with each hit's path relative to
  the share, so the desktop can show `photos/holiday.png` and open it.
- Search is started from the filter box once it has three characters and the
  person presses Enter, not on every keystroke; results list replaces the folder
  while a search is showing, with "in this share" above it and a way back.
- Cost: a walk of a filled share is a lot of `stat` calls. Cap the depth and the
  results, and put a byte/time budget in the child (stop after 2 seconds) so a
  search can't become a denial of service against the disks.
- Tests: a query with a slash or `..` is treated as text, not a path; the walk
  never leaves the root; a stopped search still answers what it found.

## 5. Slice D: kind, owner and permissions

**Built** (Nov 10), as below. Whether the person can write comes from the kernel
(`faccessat` as them), and the words for why not are read from the permissions; an
access list is named rather than read entry by entry.

The question a person actually asks is "why can't I write in here?".

- `list` already returns name, dir, size and modified. Add `mode` (the octal or
  an rwx string), `owner` and `group` from `FileInfo.Sys().(*syscall.Stat_t)`,
  resolved to names in the child (it runs as the person, and names are readable).
- Columns are opt-in: a small "columns" control in the toolbar showing Kind,
  Owner and Permissions, off by default so the list stays clean on a phone.
- The preview pane gains a line each for kind, owner and permissions, and when
  the person can't write the folder says so plainly: "Only the owner can write
  here" or "the group can write here".
- Tests: a file the person can't read still lists (the folder is readable), and
  the words for each case come from the mode, not from a guess.

## 6. Slice E: snapshots, and restoring from them

**Built** (Nov 10), as below, with the reading done by the helper rather than by
the person's own child. That is the one place the plan was wrong, and the reason
matters: ZFS leaves `.zfs` reachable to root alone on many installs
(`snapdir=hidden`, and the folder itself is `0500 root`), so asking the child to
open a snapshot as the person would have failed on a working NAS. Three things
keep the boundary instead:

- the snapshot's name comes from `fnas snapshot list`, never from the request;
- the path is resolved and confined against `<share>/.zfs/snapshot/<name>`
  through the same resolver, so a climb out is refused as "that path is outside
  media";
- the person must be able to list the file's current folder through their own
  child, which is the same permission that lets them see the file today. That is
  the check that answers for reading as root, and it still works for a file that
  has since been deleted, because it asks about the folder and not the file.

Everything written back is written by the child, as the person, through the same
door an upload takes, so a restore inherits the same refusals and the file lands
owned by whoever asked.

- `GET /api/files/snapshots?share=…&path=…` lists the versions that hold that
  path, each with its size and the date it was taken (`file-snapshots`).
- `GET /api/files/download?share=…&path=…&snapshot=…` reads one, honouring
  `Range`, so a picture or a video opens from a snapshot with no new machinery.
- **Restore a copy** (`POST /api/files/restore`) writes the saved file beside the
  current one as `notes (from 11-10-2026).txt`, never over it, and refuses a name
  that is already there rather than quietly replacing it. **Replace in place** is
  deliberately not built: a copy plus the existing delete is enough, and it can
  never lose the file the person is looking at.
- The menu item is shown disabled with "This pool doesn't support snapshots" when
  `share.snapshots` is false, which is how an array or an Unraid-style pool
  answers.
- Tests: the versions offered are only the ones that hold that path; a snapshot
  nobody made is refused by name; a climb out is refused; a snapshot with no copy
  of the file says "`hourly_2` has no copy of that file"; restoring writes by the
  copy's full path as the person, and leaves the original alone; restoring onto an
  existing name, out of the share, with no name, or into a read-only share is
  refused before anything is written.

**What was seen**, in the container as `admin` (media writable) and `guest`
(media writable, family refused): two versions of `notes.txt` listed with their
dates and sizes; the older one read as its own bytes, different from the file as
it is now; `notes (from 11-10-2026).txt` written beside it, 48 bytes, owner
`admin:media`, mode `664`, and the file itself unchanged; the same restore refused
for an invented snapshot name, for a name already in the folder, and for a target
outside the share; `guest` refused `family` with "isn't a share you can use" while
its own `media` versions listed.

**What was not seen**: a real ZFS snapshot. The harness writes `.zfs` trees by
hand, so this proves the route and the refusals, not ZFS itself — and in the
harness `.zfs` shows up in the listing, which it does not on a default install
(`snapdir=hidden`). Restoring a version from a snapshot of a *pool* that has since
been destroyed, and a restore of something larger than a few kilobytes, are also
untried.

**The 10 GB clause** is unchanged in the code (`MaxUpload`) but was validated at a
smaller size on purpose: a 1 GiB file was uploaded, downloaded again, and compared
byte for byte (same SHA-256), then read from the middle with
`Range: bytes=900000000-900000049`, which answered `206` with
`Content-Range: bytes 900000000-900000049/1073741824` and the right 50 bytes. That
exercises the same path — the counter, the deadlines cleared for the upload, the
child's temp file and rename — without waiting on ten gigabytes. The cap itself is
still only checked by reading the code.

## 7. Slice F: the installable phone app

Last, because it changes layout and input.

- A manifest (`display: standalone`, name, colours, icons at 192 and 512, maskable
  variants drawn from the existing icon set), a service worker that caches the
  shell only — never API answers, never files — and a line in the desktop's
  README about adding it to the home screen.
- Files on a phone: the shares become a drop-down rather than a column, the row
  menu becomes the primary way to act, tap targets at least 44px, and no sideways
  scrolling at 360px wide. Uploads go through the photo picker (`accept="image/*,video/*"`
  and `capture` is deliberately not set, so the person chooses).
- The gate is walked on a real phone against the VM: browse, download, open a
  photo, play a video, upload from the library, rename, move with "Move to…",
  delete, and restore an earlier version.

## 8. What is deliberately not in this plan

- Multi-user editing, locking, or a conflict warning when two people write the
  same file. Last writer wins, as on any NAS share today; say so in the docs.
- A recycle bin in Files. Deletes are permanent except where the share's SMB
  recycle bin happens to catch them, which the delete confirmation says.
- Cross-share copy or move, sharing a single file by link, or a public URL.
- Compression on download beyond the multi-file zip, and any thumbnail cache on
  disk; thumbnails are the browser's own lazy-loaded images.

## 9. How each slice is signed off

For every slice, in this order:

1. `gofmt`, `go vet` and `go test ./...` in the web module; the Python suite; both
   frontend builds. CI does the same on push.
2. Walk it in the development container as `admin` (share it can change),
   `guest` (share it can only read) and a third account with no share, checking
   that the refusal comes from the server and not only from a hidden button.
3. Add what was seen to the overview's Files passage, including what was not.
4. One line in the changelog, and the plan's Phase 12 row if a gate item moved.
