import { useContext, useState } from "preact/hooks";
import { Pool, Share, fnas, fnasJson, humanSize } from "./api";
import { AlertSetup } from "./alerting";
import { BackupJob } from "./backups";
import { DesktopContext } from "./desktop/context";
import { CreatePool, ImportPools } from "./pools";
import { CreateShare, LanSubnet, useShareHost } from "./sharing";
import { AddUser } from "./users";
import { Badge, Button, Card, Loading, Page, Result, useAction, useLoad } from "./ui";

const STEPS = ["Storage", "Network", "People and shares", "Alerts", "Backup", "Done"];

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
            <AddUser onAdded={() => {}} />
            <CreateShare onCreated={shares.reload} />
        </div>
    );
}

function DoneStep() {
    const host = useShareHost();
    const shares = useLoad(() => fnasJson<Share[]>(["share", "list"]));
    // Real names where there are shares, so the lines can be copied as they are
    const nfs = shares.data?.find(share => share.nfs);
    return (
        <Card title="Your NAS is ready">
            <p>Open shares from other computers:</p>
            <ul>
                <li>Windows: <span class="mono">{`\\\\${host}`}</span> in File Explorer</li>
                <li>Mac: Finder → Go → Connect to Server → <span class="mono">{`smb://${host}`}</span></li>
                <li>Linux: <span class="mono">{`mount ${host}:${nfs?.path || "/mnt/POOL/shares/SHARE"} /media/${nfs?.name || "SHARE"}`}</span>
                    {!nfs && <span class="muted small"> (once a share is on NFS, its own line shows here)</span>}</li>
            </ul>
            <p class="muted">Each step lives on in its own app: Storage, Users, Shares, Snapshots and backup, and Alerts. Setup
                won't open by itself again; Settings → Setup runs it again.</p>
        </Card>
    );
}

export function Setup() {
    const [step, setStep] = useState(0);
    const desktop = useContext(DesktopContext);
    const finish = useAction();
    const close = (skipped: boolean) => finish.run(async () => {
        await fnas(["setup", "done", ...(skipped ? ["--skipped"] : [])]);
        desktop.setupFinished();
        desktop.close("setup");
    });
    return (
        <Page intro="Set up storage, shares and alerts. Each step can be skipped and changed later in its own app.">
            <div class="row">
                <ol class="steps">
                    {STEPS.map((name, index) => (
                        <li key={name} class={index === step ? "current" : index < step ? "done" : ""}>{index + 1}. {name}</li>
                    ))}
                </ol>
                {step < STEPS.length - 1 && <Button busy={finish.busy} onClick={() => close(true)}>Skip setup for now</Button>}
            </div>
            {step === 0 && <StorageStep />}
            {step === 1 && <LanSubnet />}
            {step === 2 && <SharesStep />}
            {step === 3 && <AlertSetup />}
            {step === 4 && <BackupJob />}
            {step === 5 && <DoneStep />}
            <Result outcome={finish.outcome} />
            <div class="row">
                {step > 0 && <Button onClick={() => setStep(step - 1)}>Back</Button>}
                {step < STEPS.length - 1 && <Button kind="primary" onClick={() => setStep(step + 1)}>Next</Button>}
                {step === STEPS.length - 1 && <Button kind="primary" busy={finish.busy} onClick={() => close(false)}>Finish</Button>}
            </div>
        </Page>
    );
}
