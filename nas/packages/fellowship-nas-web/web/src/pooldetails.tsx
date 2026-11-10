// One pool in detail: what uses its space, how full it has been over time, and when it fills
import { useState } from "preact/hooks";
import { PoolDetails, UsagePoint, fnasJson, humanSize, loadEngines, plural, when } from "./api";
import { NO_SNAPSHOTS } from "./backups";
import { Button, Card, Loading, Notice, useLoad } from "./ui";
import { chartDay as day, useWidth } from "./charts";

const H = 240;
const PAD = { left: 64, right: 12, top: 16, bottom: 26 };

/** Used space over time as one line, with the pool's size as a dashed reference and a hover readout. */
function UsageChart({ points, hours }: { points: UsagePoint[]; hours: boolean }) {
    const [hover, setHover] = useState<number | null>(null);
    const [ref, W] = useWidth();
    const [t0, t1] = [points[0][0], points[points.length - 1][0]];
    const size = Math.max(...points.map(point => point[2]));
    const x = (t: number) => PAD.left + (t1 === t0 ? 0.5 : (t - t0) / (t1 - t0)) * (W - PAD.left - PAD.right);
    const y = (used: number) => H - PAD.bottom - used / size * (H - PAD.top - PAD.bottom);
    const line = points.map((point, index) => `${index ? "L" : "M"}${x(point[0]).toFixed(1)},${y(point[1]).toFixed(1)}`).join("");
    const area = `${line}L${x(t1).toFixed(1)},${y(0)}L${x(t0).toFixed(1)},${y(0)}Z`;
    const ticks = [0, 0.5, 1].map(share => share * size);
    const shown = hover === null ? null : points[hover];
    const pick = (event: PointerEvent) => {
        const box = (event.currentTarget as SVGElement).getBoundingClientRect();
        const at = t0 + ((event.clientX - box.left) / box.width * W - PAD.left) / (W - PAD.left - PAD.right) * (t1 - t0);
        let best = 0;
        points.forEach((point, index) => { if (Math.abs(point[0] - at) < Math.abs(points[best][0] - at)) best = index; });
        setHover(best);
    };
    return (
        <div class="chart-wrap" ref={ref}>
            <svg class="chart" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" onPointerMove={pick} onPointerLeave={() => setHover(null)}
                 aria-label={`Space used, from ${humanSize(points[0][1])} to ${humanSize(points[points.length - 1][1])}`}>
                {ticks.map(tick => (
                    <g key={tick}>
                        <line class={tick === size ? "size" : "grid"} x1={PAD.left} x2={W - PAD.right} y1={y(tick)} y2={y(tick)} />
                        <text x={PAD.left - 6} y={y(tick) + 4} text-anchor="end">{humanSize(tick)}</text>
                    </g>
                ))}
                <text x={W - PAD.right} y={y(size) - 4} text-anchor="end">Size</text>
                <path class="area" d={area} />
                <path class="line" d={line} />
                <text x={PAD.left} y={H - 6}>{day(t0, hours)}</text>
                <text x={(PAD.left + W - PAD.right) / 2} y={H - 6} text-anchor="middle">{day((t0 + t1) / 2, hours)}</text>
                <text x={W - PAD.right} y={H - 6} text-anchor="end">{day(t1, hours)}</text>
                {shown && <>
                    <line class="cross" x1={x(shown[0])} x2={x(shown[0])} y1={PAD.top} y2={H - PAD.bottom} />
                    <circle class="dot" cx={x(shown[0])} cy={y(shown[1])} r={4} />
                </>}
            </svg>
            {shown && (
                <div class="chart-tip" style={{ left: `${x(shown[0]) / W * 100}%`, top: `${y(shown[1]) / H * 100}%` }}>
                    {day(shown[0], hours)}: <strong>{humanSize(shown[1])}</strong> of {humanSize(shown[2])}
                </div>
            )}
        </div>
    );
}

const PARTS = [
    { key: "shares", label: "Files in shares", color: "var(--series-1)" },
    { key: "snapshots", label: "Earlier versions (snapshots)", color: "var(--series-2)" },
    { key: "backups", label: "Backups of other pools", color: "var(--series-3)" },
    { key: "other", label: "Home folders, settings and the rest", color: "var(--series-4)" },
] as const;

/** Used and free space as one bar, each part with its own colour, label and amount. */
function SpaceBar({ details }: { details: PoolDetails }) {
    const { parts, free, used } = details.space;
    const total = used + free;
    const percent = (bytes: number) => total ? bytes / total * 100 : 0;
    return (
        <div class="stack">
            <div class="spacebar" role="img" aria-label={`${humanSize(used)} used, ${humanSize(free)} free`}>
                {PARTS.filter(part => parts[part.key] > 0).map(part => (
                    <span key={part.key} title={`${part.label}: ${humanSize(parts[part.key])}`}
                          style={{ width: `${percent(parts[part.key])}%`, background: part.color }} />
                ))}
            </div>
            <div class="legend">
                {PARTS.map(part => (
                    <div class="legend-row" key={part.key}>
                        <span class="swatch" style={{ background: part.color }} />
                        <span>{part.label}</span>
                        <span class="muted small">{humanSize(parts[part.key])} · {percent(parts[part.key]).toFixed(0)}%</span>
                    </div>
                ))}
                <div class="legend-row">
                    <span class="swatch free" />
                    <span>Free</span>
                    <span class="muted small">{humanSize(free)} · {percent(free).toFixed(0)}%</span>
                </div>
            </div>
        </div>
    );
}

export function PoolDetailsView({ name, back }: { name: string; back: () => void }) {
    const details = useLoad(() => Promise.all([fnasJson<PoolDetails>(["pool", "details", name]), loadEngines()]));
    const [range, setRange] = useState<"hourly" | "daily">("hourly");
    return (
        <div class="stack">
            <div class="row">
                <Button onClick={back}>← Storage</Button>
                <Button onClick={details.reload}>Refresh</Button>
            </div>
            <Loading loaded={details}>{() => {
                const [pool, engines] = details.data!;
                const engine = engines[pool.engine];
                const space = pool.space;
                // The format an engine put on top of its disks, by its own name for it
                const format = engine.filesystems?.find(each => each.name === pool.filesystem)?.title;
                const points = pool.history[range];
                const fill = pool.projection;
                return <>
                    <Card title={pool.name}>
                        <div class="stats">
                            <div class="stat"><span class="small muted">Used</span>
                                <strong>{humanSize(space.used)}</strong>
                                <span class="small muted">{Math.round(space.used * 100 / (space.used + space.free))}% of {humanSize(space.used + space.free)}</span></div>
                            <div class="stat"><span class="small muted">Free</span><strong>{humanSize(space.free)}</strong></div>
                            <div class="stat"><span class="small muted">Full in</span>
                                <strong>{fill.days === undefined ? "–" : fill.days > 3650 ? "10+ years" : plural(fill.days, "day")}</strong>
                                <span class="small muted">{fill.days === undefined ? fill.reason
                                    : `at ${humanSize(fill.per_day!)} a day, the recent rate`}</span></div>
                            <div class="stat"><span class="small muted">Compression</span>
                                <strong>{!engine.compresses ? "–" : `${pool.compression.toFixed(2)}×`}</strong>
                                <span class="small muted">{!engine.compresses ? `${format || engine.title} doesn't compress`
                                    : pool.logical > space.used ? `saves ${humanSize(pool.logical - space.used)}`
                                    : "saves nothing yet"}</span></div>
                        </div>
                    </Card>
                    <Card title="Space used over time" actions={
                        <div class="segmented" role="group" aria-label="Range">
                            <button type="button" class={range === "hourly" ? "current" : ""} aria-pressed={range === "hourly"}
                                    onClick={() => setRange("hourly")}>Week</button>
                            <button type="button" class={range === "daily" ? "current" : ""} aria-pressed={range === "daily"}
                                    onClick={() => setRange("daily")}>Year</button>
                        </div>}>
                        {points.length < 2
                            ? <Notice>Not enough history to draw yet. The hourly check records how full {pool.name} is,
                                {range === "hourly" ? " once an hour" : " once a day"}; the chart fills in from then on.</Notice>
                            : <UsageChart points={points} hours={range === "hourly"} />}
                    </Card>
                    <Card title="What uses the space">
                        <SpaceBar details={pool} />
                        {space.shares.length > 0 && (
                            <div class="table-wrap">
                                <table>
                                    <thead><tr><th>Share</th><th>Files</th><th>Earlier versions</th></tr></thead>
                                    <tbody>{space.shares.map(share => (
                                        <tr key={share.name}><td>{share.name}</td><td>{humanSize(share.files)}</td>
                                            <td>{humanSize(share.snapshots)}</td></tr>
                                    ))}</tbody>
                                </table>
                            </div>
                        )}
                    </Card>
                    <Card title="About this pool">
                        <div class="table-wrap">
                            <table><tbody>
                                <tr><th scope="row">Kind of storage</th><td>{engine.title}{format ? `, formatted ${format}` : ""}</td></tr>
                                <tr><th scope="row">Created</th><td>{pool.created ? when(pool.created) : "–"}</td></tr>
                                <tr><th scope="row">Last {engine.check_name}</th>
                                    <td>{pool.last_scrub ? when(pool.last_scrub) : "never"}</td></tr>
                                <tr><th scope="row">{engine.errors_label}</th>
                                    <td>{pool.errors}</td></tr>
                                <tr><th scope="row">Fragmentation of free space</th><td>{pool.fragmentation === null
                                    ? <span class="muted small">{format ? `${format} doesn't report it` : "–"}</span>
                                    : <>{pool.fragmentation}%<span class="muted small"> (higher slows writes as a pool fills)</span></>}</td></tr>
                                {!engine.snapshots && <tr><th scope="row">Snapshots</th><td class="muted small">{NO_SNAPSHOTS}</td></tr>}
                            </tbody></table>
                        </div>
                    </Card>
                </>;
            }}</Loading>
        </div>
    );
}
