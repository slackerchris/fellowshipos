import { useState } from "preact/hooks";
import { Pool, Share, fnas, fnasJson, humanSize } from "./api";
import { AlertSetup } from "./alerting";
import { CreatePool, ImportPools } from "./pools";
import { CreateShare, LanSubnet, SmbPassword } from "./sharing";
import { Badge, Button, Card, Field, Loading, Notice, Page, Result, useAction, useLoad } from "./ui";

const STEPS = ["Storage", "Network", "Shares", "Alerts", "Backup", "Done"];

function StorageStep() {
    const pools = useLoad(() => fnasJson<Pool[]>(["pool", "list"]));
    const [adding, setAdding] = useState(false);
    return (
        <Loading loaded={pools}>{() => (
            <div class="stack">
                {pools.data!.length > 0 && (
                    <Card title="Your pools">
                        {pools.data!.map(pool => (
                            <div key={pool.name} class="row">
                                <strong>{pool.name}</strong>
                                <Badge kind={pool.health === "ONLINE" ? "good" : "warning"}>{pool.health.toLowerCase()}</Badge>
                                <span class="muted small">{humanSize(pool.size)}</span>
                            </div>
                        ))}
                    </Card>
                )}
                <ImportPools onChange={pools.reload} />
                {pools.data!.length === 0 || adding
                    ? <CreatePool taken={pools.data!.map(pool => pool.name)} onCreated={() => { setAdding(false); pools.reload(); }} />
                    : <div class="row"><Button onClick={() => setAdding(true)}>Create another pool</Button></div>}
            </div>
        )}</Loading>
    );
}

function SharesStep() {
    const shares = useLoad(() => fnasJson<Share[]>(["share", "list"]));
    return (
        <div class="stack">
            <Loading loaded={shares}>{() => shares.data!.length > 0 && (
                <Card title="Your shares">
                    {shares.data!.map(share => (
                        <div key={share.dataset} class="row">
                            <strong>{share.name}</strong>
                            <span class="muted small">on {share.pool}, {[share.smb && "SMB", share.nfs && "NFS"].filter(Boolean).join(" and ")}</span>
                        </div>
                    ))}
                </Card>
            )}</Loading>
            <CreateShare onCreated={shares.reload} />
            <SmbPassword />
        </div>
    );
}

function BackupStep() {
    const pools = useLoad(() => fnasJson<Pool[]>(["pool", "list"]));
    const job = useLoad(() => fnasJson<{ job: { source: string; target: string } | null }>(["backup", "show"]));
    const [source, setSource] = useState("");
    const [target, setTarget] = useState("");
    const action = useAction(job.reload);
    return (
        <Card title="Nightly backup">
            <p class="muted small">Snapshots protect against deleted files, not against losing a pool. A second pool, such as a
                USB disk, can hold a copy of every share.</p>
            <Loading loaded={pools}>{() => <Loading loaded={job}>{() => {
                const writable = pools.data!.filter(pool => pool.readonly === "off").map(pool => pool.name);
                if (job.data!.job) {
                    return <Notice kind="done">Shares on {job.data!.job.source} are backed up to {job.data!.job.target} every night.</Notice>;
                }
                if (writable.length < 2) {
                    return <Notice>A backup needs a second pool. You can skip this and add one later from Storage.</Notice>;
                }
                const from = source || writable[0];
                const to = target || writable.find(pool => pool !== from)!;
                return (
                    <form class="stack" onSubmit={event => {
                        event.preventDefault();
                        action.run(() => fnas(["backup", "set", from, to]));
                    }}>
                        <div class="grid-2">
                            <Field label="Back up shares on">
                                <select value={from} onChange={event => setSource(event.currentTarget.value)}>
                                    {writable.map(pool => <option key={pool}>{pool}</option>)}
                                </select>
                            </Field>
                            <Field label="To">
                                <select value={to} onChange={event => setTarget(event.currentTarget.value)}>
                                    {writable.filter(pool => pool !== from).map(pool => <option key={pool}>{pool}</option>)}
                                </select>
                            </Field>
                        </div>
                        <div class="row"><Button kind="primary" type="submit" busy={action.busy}>Back up every night</Button></div>
                        <Result outcome={action.outcome} />
                    </form>
                );
            }}</Loading>}</Loading>
        </Card>
    );
}

function DoneStep() {
    const host = window.location.hostname;
    return (
        <Card title="Your NAS is ready">
            <p>Open shares from other computers:</p>
            <ul>
                <li>Windows: <span class="mono">{`\\\\${host}`}</span> in File Explorer</li>
                <li>Mac: Finder → Go → Connect to Server → <span class="mono">{`smb://${host}`}</span></li>
                <li>Linux: <span class="mono">{`mount ${host}:/POOL/shares/NAME /mnt`}</span></li>
            </ul>
            <p class="muted">Storage, Shares, Health and Alerts are on the desktop. Run this setup again any time, for example after adding disks.</p>
        </Card>
    );
}

export function Setup() {
    const [step, setStep] = useState(0);
    return (
        <Page intro="Set up storage, shares and alerts. Each step can be skipped and changed later.">
            <ol class="steps">
                {STEPS.map((name, index) => (
                    <li key={name} class={index === step ? "current" : index < step ? "done" : ""}>{index + 1}. {name}</li>
                ))}
            </ol>
            {step === 0 && <StorageStep />}
            {step === 1 && <LanSubnet />}
            {step === 2 && <SharesStep />}
            {step === 3 && <AlertSetup />}
            {step === 4 && <BackupStep />}
            {step === 5 && <DoneStep />}
            <div class="row">
                {step > 0 && <Button onClick={() => setStep(step - 1)}>Back</Button>}
                {step < STEPS.length - 1 && <Button kind="primary" onClick={() => setStep(step + 1)}>Next</Button>}
            </div>
        </Page>
    );
}

