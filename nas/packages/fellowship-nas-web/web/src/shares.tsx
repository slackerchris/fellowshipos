import { useState } from "preact/hooks";
import { FnasError, Policy, Share, fnas, fnasJson } from "./api";
import { CreateShare, LanSubnet, SmbPassword } from "./sharing";
import { Badge, Button, Card, Confirm, Field, Loading, Page, Result, useAction, useLoad } from "./ui";

const PERIODS: (keyof Policy)[] = ["hourly", "daily", "monthly"];

function PolicyEditor({ share, onDone }: { share: Share; onDone: () => void }) {
    const [policy, setPolicy] = useState<Policy>(share.policy);
    const action = useAction(onDone);
    return (
        <form class="stack" onSubmit={event => {
            event.preventDefault();
            action.run(() => fnas(["snapshot", "policy", share.name,
                                   ...PERIODS.flatMap(period => [`--${period}`, String(policy[period])])]));
        }}>
            <div class="grid-2">
                {PERIODS.map(period => (
                    <Field key={period} label={`${period[0].toUpperCase()}${period.slice(1)} snapshots kept`}>
                        <input type="number" min={0} max={1000} value={policy[period]}
                               onInput={event => setPolicy({ ...policy, [period]: Number(event.currentTarget.value) })} />
                    </Field>
                ))}
            </div>
            <p class="muted small">Snapshots keep earlier versions of files. Set all three to 0 to stop taking them.</p>
            <div class="row">
                <Button kind="primary" type="submit" busy={action.busy}>Save</Button>
                <Button onClick={onDone}>Cancel</Button>
            </div>
            <Result outcome={action.outcome} />
        </form>
    );
}

/** fnas states the exact phrase and what will be lost, so ask it rather than guess. */
async function deletionTerms(name: string): Promise<{ phrase: string; lost: string[] }> {
    try {
        await fnas(["share", "delete", name]);
    } catch (error) {
        const text = (error as FnasError).message;
        const phrase = /--confirm "([^"]+)"/.exec(text);
        if (phrase) {
            const lost = text.split("\n").filter(line => line.startsWith("    ")).map(line => line.trim());
            return { phrase: phrase[1], lost };
        }
        throw error;
    }
    throw new FnasError("fnas didn't ask for confirmation");
}

function ShareList({ shares, reload }: { shares: Share[]; reload: () => void }) {
    const [editing, setEditing] = useState<string | null>(null);
    const [deleting, setDeleting] = useState<{ name: string; phrase: string; lost: string[] } | null>(null);
    const action = useAction(reload);
    const deleteAction = useAction(reload);
    const host = window.location.hostname;

    if (!shares.length) {
        return <Card title="Shares"><p class="muted">No shares yet.</p></Card>;
    }
    const toggle = (share: Share, protocol: "nfs" | "smb") =>
        action.run(() => fnas(["share", "set", share.name, `--${protocol}`, share[protocol] ? "off" : "on"]));

    return (
        <Card title="Shares">
            <div class="table-wrap">
                <table>
                    <thead><tr><th>Share</th><th>Shared with</th><th>Snapshots kept</th><th /></tr></thead>
                    <tbody>
                        {shares.map(share => (
                            <tr key={share.dataset}>
                                <td>
                                    <strong>{share.name}</strong> <Badge kind="neutral">{share.mode === "shared" ? "everyone" : "one owner"}</Badge>
                                    {share.readonly && <> <Badge kind="neutral">read-only</Badge></>}
                                    <br /><span class="small muted mono">{share.path}</span>
                                    {share.smb && <><br /><span class="small muted mono">{`\\\\${host}\\${share.name}`}</span></>}
                                    {share.nfs && <><br /><span class="small muted mono">{`${host}:${share.path}`}</span></>}
                                </td>
                                <td>
                                    <div class="stack">
                                        <label class="check">
                                            <input type="checkbox" checked={share.smb} disabled={action.busy || share.readonly}
                                                   onChange={() => toggle(share, "smb")} />
                                            <span>SMB</span>
                                        </label>
                                        <label class="check">
                                            <input type="checkbox" checked={share.nfs} disabled={action.busy || share.readonly}
                                                   onChange={() => toggle(share, "nfs")} />
                                            <span>NFS</span>
                                        </label>
                                    </div>
                                </td>
                                <td>
                                    {editing === share.name
                                        ? <PolicyEditor share={share} onDone={() => { setEditing(null); reload(); }} />
                                        : <span>{share.policy.hourly} hourly, {share.policy.daily} daily, {share.policy.monthly} monthly</span>}
                                </td>
                                <td class="actions">
                                    {!share.readonly && editing !== share.name && (
                                        <div class="row row-end">
                                            <Button onClick={() => setEditing(share.name)}>Snapshots</Button>
                                            <Button busy={deleteAction.busy && !deleting} onClick={() => deleteAction.run(async () => {
                                                setDeleting({ name: share.name, ...await deletionTerms(share.name) });
                                            })}>Delete</Button>
                                        </div>
                                    )}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <Result outcome={action.outcome} />
            {!deleting && <Result outcome={deleteAction.outcome} />}
            {deleting && (
                <Confirm title="Delete share" phrase={deleting.phrase} busy={deleteAction.busy} outcome={deleteAction.outcome}
                         onCancel={() => { setDeleting(null); deleteAction.clear(); }}
                         onConfirm={typed => deleteAction.run(async () => {
                             const output = await fnas(["share", "delete", deleting.name, "--confirm", typed]);
                             setDeleting(null);
                             return output;
                         })}>
                    <p>Every file in <strong>{deleting.name}</strong> is destroyed, along with its earlier versions:</p>
                    {deleting.lost.length
                        ? <ul class="small mono">{deleting.lost.map(snapshot => <li key={snapshot}>{snapshot}</li>)}</ul>
                        : <p class="muted small">It has no snapshots.</p>}
                    <p>A backup on another pool, if there is one, is kept.</p>
                </Confirm>
            )}
        </Card>
    );
}

export function Shares() {
    const shares = useLoad(() => fnasJson<Share[]>(["share", "list"]));
    return (
        <Page intro="Folders that computers on your home network can open.">
            <Loading loaded={shares}>{() => <ShareList shares={shares.data!} reload={shares.reload} />}</Loading>
            <CreateShare onCreated={shares.reload} />
            <SmbPassword />
            <LanSubnet />
        </Page>
    );
}

