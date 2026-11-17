import { useState } from "preact/hooks";
import { Alert, fnas, fnasJson, when } from "./api";
import { AlertSetup } from "./alerting";
import { SEVERITY } from "./health";
import { Badge, Button, Card, Confirm, Loading, Page, useAction, useLoad } from "./ui";

function RecentAlerts({ alerts, reload }: { alerts: Alert[]; reload: () => void }) {
    const [clearing, setClearing] = useState(false);
    const clear = useAction(() => { setClearing(false); reload(); });
    const dismiss = useAction(reload);
    return (
        <Card title="Recent alerts" actions={
            <span class="row">
                <Button onClick={reload}>Refresh</Button>
                <Button disabled={!alerts.length} onClick={() => setClearing(true)}>Clear history</Button>
            </span>}>
            {!alerts.length && <p class="muted">None yet.</p>}
            {[...alerts].reverse().map(alert => (
                <div key={`${alert.time}-${alert.title}`}>
                    <Badge kind={SEVERITY[alert.severity]}>{alert.severity}</Badge> <strong>{alert.title}</strong>{" "}
                    <span class="small muted">{when(alert.time)}</span>
                    {alert.dismissed && <span class="small muted"> · dismissed on the desktop</span>}
                    <p class="small muted">{alert.message}</p>
                    {alert.undelivered?.length > 0 && <p class="small">Not delivered: {alert.undelivered.join("; ")}</p>}
                    {!alert.dismissed && <Button busy={dismiss.busy} onClick={() => dismiss.run(() =>
                        fnas(["alert", "dismiss", "--time", String(alert.time), "--title", alert.title]))}>
                        Dismiss</Button>}
                </div>
            ))}
            {clearing && (
                <Confirm title="Clear history" phrase="clear" busy={clear.busy} outcome={clear.outcome}
                         onCancel={() => { setClearing(false); clear.clear(); }}
                         onConfirm={() => clear.run(() => fnas(["alert", "clear"]))}>
                    <p>Empties the record of what was sent. Health keeps showing anything that is wrong now.</p>
                </Confirm>
            )}
        </Card>
    );
}

export function Alerts() {
    const alerts = useLoad(() => fnasJson<Alert[]>(["alert", "list", "--limit", "50", "--all"]));
    return (
        <Page intro="Where alerts go, and what was sent. Checks run every hour.">
            <AlertSetup />
            <Loading loaded={alerts}>{() => <RecentAlerts alerts={alerts.data!} reload={alerts.reload} />}</Loading>
        </Page>
    );
}
