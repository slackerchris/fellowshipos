import { Alert, fnasJson, when } from "./api";
import { AlertSetup } from "./alerting";
import { SEVERITY } from "./health";
import { Badge, Button, Card, Loading, Page, useLoad } from "./ui";

function RecentAlerts({ alerts, reload }: { alerts: Alert[]; reload: () => void }) {
    return (
        <Card title="Recent alerts" actions={<Button onClick={reload}>Refresh</Button>}>
            {!alerts.length && <p class="muted">None yet.</p>}
            {[...alerts].reverse().map(alert => (
                <div key={`${alert.time}-${alert.title}`}>
                    <Badge kind={SEVERITY[alert.severity]}>{alert.severity}</Badge> <strong>{alert.title}</strong>{" "}
                    <span class="small muted">{when(alert.time)}</span>
                    <p class="small muted">{alert.message}</p>
                    {alert.undelivered?.length > 0 && <p class="small">Not delivered: {alert.undelivered.join("; ")}</p>}
                </div>
            ))}
        </Card>
    );
}

export function Alerts() {
    const alerts = useLoad(() => fnasJson<Alert[]>(["alert", "list", "--limit", "50"]));
    return (
        <Page intro="Where alerts go, and what was sent. Checks run every hour.">
            <AlertSetup />
            <Loading loaded={alerts}>{() => <RecentAlerts alerts={alerts.data!} reload={alerts.reload} />}</Loading>
        </Page>
    );
}
