// Gauges, dials and trends, drawn as SVG so they stay sharp at any window size.
// Health uses these for its overview and its detail views.
import { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";

export type Kind = "good" | "warning" | "bad" | "neutral" | "accent";

/** A short date for a chart's axis or its hover readout. */
export function chartDay(stamp: number, hours: boolean) {
    const date = new Date(stamp * 1000);
    return hours ? date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
        : date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

/** The width an element is drawn at, so a chart's text stays its real size. */
export function useWidth(fallback = 640) {
    const ref = useRef<HTMLDivElement>(null);
    const [width, setWidth] = useState(fallback);
    useEffect(() => {
        const observer = new ResizeObserver(([entry]) => setWidth(Math.max(240, Math.round(entry.contentRect.width))));
        if (ref.current) observer.observe(ref.current);
        return () => observer.disconnect();
    }, []);
    return [ref, width] as const;
}

/** How often something happened, split by how bad it is. */
export interface Part {
    value: number;
    kind: Kind;
}

const DIAL = { size: 88, stroke: 9, gap: 3 };

function ring(size: number, stroke: number) {
    const radius = (size - stroke) / 2;
    return { radius, length: 2 * Math.PI * radius, centre: size / 2 };
}

function Figure({ children, centre, size }: { children: ComponentChildren; centre: string; size: number }) {
    return (
        <span class="gauge-figure">
            <svg class="gauge-dial" width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
                {children}
            </svg>
            <span class="gauge-value">{centre}</span>
        </span>
    );
}

function Track({ size, stroke }: { size: number; stroke: number }) {
    const { radius, centre } = ring(size, stroke);
    return <circle class="gauge-track" cx={centre} cy={centre} r={radius} stroke-width={stroke} fill="none" />;
}

/**
 * A ring filled to a percentage. The number sits in the middle, and the words
 * under it carry the same meaning, so colour alone never tells the story.
 */
export function Gauge({ percent, centre, label, note, kind = "good", spark, size = DIAL.size, stroke = DIAL.stroke,
                       onClick }: {
    percent: number;
    centre: string;
    label?: string;
    note?: string;
    kind?: Kind;
    // The shape of the same number over time, when there is history to draw
    spark?: [number, number][];
    size?: number;
    stroke?: number;
    onClick?: () => void;
}) {
    const { radius, length, centre: middle } = ring(size, stroke);
    const filled = Math.max(0, Math.min(100, percent));
    const body = (
        <>
            <Figure centre={centre} size={size}>
                <Track size={size} stroke={stroke} />
                <circle class={`gauge-fill kind-${kind}`} cx={middle} cy={middle} r={radius}
                        stroke-width={stroke} fill="none" stroke-linecap="round"
                        stroke-dasharray={`${(length * filled) / 100} ${length}`}
                        transform={`rotate(-90 ${middle} ${middle})`} />
            </Figure>
            {label && <span class="gauge-label">{label}</span>}
            {note && <span class="small muted">{note}</span>}
            {spark && spark.length > 1 && <Sparkline points={spark} kind={kind} />}
        </>
    );
    return onClick
        ? <button type="button" class="gauge" onClick={onClick}>{body}</button>
        : <div class="gauge">{body}</div>;
}

/** A ring split into its parts, for counts rather than one percentage. */
export function Dial({ parts, centre, label, note, onClick }: {
    parts: Part[];
    centre: string;
    label: string;
    note?: string;
    onClick?: () => void;
}) {
    const { radius, length, centre: middle } = ring(DIAL.size, DIAL.stroke);
    const total = parts.reduce((sum, part) => sum + part.value, 0);
    const shown = parts.filter(part => part.value > 0);
    let run = 0;
    const arcs = shown.map((part, index) => {
        const span = total ? (length * part.value) / total : 0;
        const arc = (
            <circle key={index} class={`gauge-fill kind-${part.kind}`} cx={middle} cy={middle} r={radius}
                    stroke-width={DIAL.stroke} fill="none"
                    // A small gap, so two neighbours of the same colour stay two parts.
                    // A whole ring needs none: the gap would look like a notch.
                    stroke-dasharray={`${shown.length > 1 ? Math.max(span - DIAL.gap, 1) : span} ${length}`}
                    stroke-dashoffset={-run}
                    transform={`rotate(-90 ${middle} ${middle})`} />
        );
        run += span;
        return arc;
    });
    const body = (
        <>
            <Figure centre={centre} size={DIAL.size}><Track size={DIAL.size} stroke={DIAL.stroke} />{arcs}</Figure>
            <span class="gauge-label">{label}</span>
            {note && <span class="small muted">{note}</span>}
        </>
    );
    return onClick
        ? <button type="button" class="gauge" onClick={onClick}>{body}</button>
        : <div class="gauge">{body}</div>;
}

/**
 * A half ring with a needle, for a percentage that has no ceiling worth
 * drawing. The number sits under the pivot, the way a dial reads.
 */
export function ArcGauge({ percent, centre, kind = "good", thin = false, ticks = false }: {
    percent: number;
    centre: string;
    kind?: Kind;
    // A hairline arc with speedometer marks, for a place with less room
    thin?: boolean;
    ticks?: boolean;
}) {
    const W = 220;
    const H = 152;
    const radius = 84;
    const stroke = thin ? 6 : 15;
    const cx = W / 2;
    const cy = 100;
    const value = Math.max(0, Math.min(100, percent));
    const length = Math.PI * radius;
    const angle = Math.PI * (1 - value / 100);
    const tip = [cx + (radius - 16) * Math.cos(angle), cy - (radius - 16) * Math.sin(angle)];
    const marks = [0, 0.25, 0.5, 0.75, 1].map(share => {
        const at = Math.PI * (1 - share);
        const inner = radius + 6;
        const outer = radius + 14;
        return { at, inner, outer };
    });
    return (
        <svg class="arc" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${centre} of 100 percent`}>
            {ticks && marks.map(mark => (
                <line key={mark.at} class="tick"
                      x1={(cx + mark.inner * Math.cos(mark.at)).toFixed(1)}
                      y1={(cy - mark.inner * Math.sin(mark.at)).toFixed(1)}
                      x2={(cx + mark.outer * Math.cos(mark.at)).toFixed(1)}
                      y2={(cy - mark.outer * Math.sin(mark.at)).toFixed(1)} />
            ))}
            <path class="arc-track" d={`M${cx - radius},${cy} A${radius},${radius} 0 0 1 ${cx + radius},${cy}`}
                  fill="none" stroke-width={stroke} stroke-linecap="round" />
            <path class={`arc-value kind-${kind}`}
                  d={`M${cx - radius},${cy} A${radius},${radius} 0 0 1 ${cx + radius},${cy}`}
                  fill="none" stroke-width={stroke} stroke-linecap="round"
                  stroke-dasharray={`${(length * value) / 100} ${length}`} />
            <line class="needle" x1={cx} y1={cy} x2={tip[0].toFixed(1)} y2={tip[1].toFixed(1)} />
            <circle class="needle-pivot" cx={cx} cy={cy} r={5.5} />
            <text class="arc-readout" x={cx} y={cy + 40} text-anchor="middle">{centre}</text>
        </svg>
    );
}

/** Points over time with no line between them, and a level worth marking. */
export function DotChart({ points, format, label, max, guide, guideLabel, hours = false }: {
    points: [number, number][];
    format: (value: number) => string;
    label: string;
    max?: number;
    guide?: number;
    guideLabel?: string;
    hours?: boolean;
}) {
    const [hover, setHover] = useState<number | null>(null);
    const [ref, W] = useWidth(320);
    const H = 132;
    const PAD = { left: 36, right: 10, top: 18, bottom: 22 };
    const high = Math.max(max ?? 0, ...points.map(point => point[1]), 0.1);
    const [t0, t1] = [points[0][0], points[points.length - 1][0]];
    const x = (t: number) => PAD.left + (t1 === t0 ? 0.5 : (t - t0) / (t1 - t0)) * (W - PAD.left - PAD.right);
    const y = (value: number) => H - PAD.bottom - (value / high) * (H - PAD.top - PAD.bottom);
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
            <svg class="dot-chart" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img"
                 onPointerMove={pick} onPointerLeave={() => setHover(null)}
                 aria-label={`${label}, from ${format(points[0][1])} to ${format(points[points.length - 1][1])}`}>
                {[0, 0.5, 1].map(share => (
                    <g key={share}>
                        <line class="grid" x1={PAD.left} x2={W - PAD.right} y1={y(share * high)} y2={y(share * high)} />
                        <text x={PAD.left - 6} y={y(share * high) + 4} text-anchor="end">{format(share * high)}</text>
                    </g>
                ))}
                {guide !== undefined && <>
                    <line class="guide" x1={PAD.left} x2={W - PAD.right} y1={y(guide)} y2={y(guide)} />
                    {guideLabel && <text class="guide-label" x={W - PAD.right} y={y(guide) - 5}
                                          text-anchor="end">{guideLabel}</text>}
                </>}
                {points.map((point, index) => (
                    <circle key={point[0]} class={shown && shown[0] === point[0] ? "dot current" : "dot"}
                            cx={x(point[0]).toFixed(1)} cy={y(point[1]).toFixed(1)} r={shown && shown[0] === point[0] ? 4 : 2.6} />
                ))}
                <text x={PAD.left} y={H - 5}>{chartDay(t0, hours)}</text>
                <text x={W - PAD.right} y={H - 5} text-anchor="end">{chartDay(t1, hours)}</text>
            </svg>
            {shown && (
                <div class="chart-tip" style={{ left: `${x(shown[0]) / W * 100}%`, top: `${y(shown[1]) / H * 100}%` }}>
                    {chartDay(shown[0], hours)}: <strong>{format(shown[1])}</strong>
                </div>
            )}
        </div>
    );
}

const ICONS = {
    // A processor: a square die with its pins
    cpu: <><rect x="7" y="7" width="10" height="10" rx="2" />
        <path d="M10 3v2M14 3v2M10 19v2M14 19v2M3 10h2M3 14h2M19 10h2M19 14h2" /></>,
    // Memory: a stick with its contacts
    memory: <><rect x="3" y="8" width="18" height="9" rx="1.5" /><path d="M7 17v3M12 17v3M17 17v3M7 8V5M17 8V5" /></>,
    // A line rising and falling
    load: <path d="M4 20V13M9 20V8M14 20v-5M19 20V10" />,
    // Stacked layers
    pools: <><path d="M12 3l9 5-9 5-9-5 9-5z" /><path d="M3 12.5l9 5 9-5M3 17l9 5 9-5" /></>,
    // A drive in a bay
    disk: <><rect x="3" y="6" width="18" height="12" rx="2" /><path d="M7 14h4" /><circle cx="17" cy="12" r="1.1" /></>,
    // A cog
    services: <><circle cx="12" cy="12" r="3.2" />
        <path d="M12 2.5v2.6M12 18.9v2.6M2.5 12h2.6M18.9 12h2.6M5.1 5.1l1.9 1.9M17 17l1.9 1.9M18.9 5.1L17 7M7 17l-1.9 1.9" /></>,
    // A drum, as a backup is often drawn
    backup: <><ellipse cx="12" cy="6.5" rx="7" ry="2.8" /><path d="M5 6.5v11c0 1.6 3.1 2.8 7 2.8s7-1.2 7-2.8v-11" /></>,
    bell: <><path d="M6 17v-5a6 6 0 0 1 12 0v5l1.5 2.5h-15z" /><path d="M10 21.5a2 2 0 0 0 4 0" /></>,
    check: <path d="M5 12.5l4.5 4.5L19 7" />,
} as const;

/** A small line icon for a card's heading, or for a row in the footer. */
export function MetricIcon({ name, size = 20 }: { name: keyof typeof ICONS; size?: number }) {
    return (
        <svg class="metric-icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            {ICONS[name]}
        </svg>
    );
}

/** A line with no axes: the shape of a measurement at a glance. */
export function Sparkline({ points, kind = "good" }: { points: [number, number][]; kind?: Kind }) {
    const W = 100;
    const H = 28;
    const high = Math.max(...points.map(point => point[1]));
    const low = Math.min(...points.map(point => point[1]));
    const span = high - low || 1;
    const line = points.map((point, index) => {
        const x = points.length > 1 ? (index / (points.length - 1)) * W : W / 2;
        const y = H - ((point[1] - low) / span) * (H - 4) - 2;
        return `${index ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
    }).join("");
    const area = `${line}L${W},${H}L0,${H}Z`;
    return (
        <svg class={`sparkline kind-${kind}`} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
            <path class="area" d={area} />
            <path class="line" d={line} vector-effect="non-scaling-stroke" />
        </svg>
    );
}

const H = 180;
const PAD = { left: 62, right: 12, top: 14, bottom: 24 };

/**
 * One measurement over time, with an optional dashed line for the level that
 * matters (the pool's size, all the processors busy) and a hover readout.
 * Draw it with at least two points; callers show an explanation until there are.
 */
export function TrendChart({ points, hours, format, label, max, guide, kind = "good" }: {
    points: [number, number][];
    hours: boolean;
    format: (value: number) => string;
    label: string;
    max?: number;
    guide?: number;
    kind?: Kind;
}) {
    const [hover, setHover] = useState<number | null>(null);
    const [ref, W] = useWidth();
    const [t0, t1] = [points[0][0], points[points.length - 1][0]];
    const high = Math.max(max ?? 0, ...points.map(point => point[1]), 1);
    const x = (t: number) => PAD.left + (t1 === t0 ? 0.5 : (t - t0) / (t1 - t0)) * (W - PAD.left - PAD.right);
    const y = (value: number) => H - PAD.bottom - (value / high) * (H - PAD.top - PAD.bottom);
    const line = points.map((point, index) => `${index ? "L" : "M"}${x(point[0]).toFixed(1)},${y(point[1]).toFixed(1)}`).join("");
    const area = `${line}L${x(t1).toFixed(1)},${y(0)}L${x(t0).toFixed(1)},${y(0)}Z`;
    const ticks = [0, 0.5, 1].map(share => share * high);
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
            <svg class={`chart kind-${kind}`} width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img"
                 onPointerMove={pick} onPointerLeave={() => setHover(null)}
                 aria-label={`${label}, from ${format(points[0][1])} to ${format(points[points.length - 1][1])}`}>
                {ticks.map(tick => (
                    <g key={tick}>
                        <line class="grid" x1={PAD.left} x2={W - PAD.right} y1={y(tick)} y2={y(tick)} />
                        <text x={PAD.left - 6} y={y(tick) + 4} text-anchor="end">{format(tick)}</text>
                    </g>
                ))}
                {guide !== undefined && (
                    <line class="guide" x1={PAD.left} x2={W - PAD.right} y1={y(guide)} y2={y(guide)} />
                )}
                <path class="area" d={area} />
                <path class="line" d={line} />
                <text x={PAD.left} y={H - 6}>{chartDay(t0, hours)}</text>
                <text x={(PAD.left + W - PAD.right) / 2} y={H - 6} text-anchor="middle">{chartDay((t0 + t1) / 2, hours)}</text>
                <text x={W - PAD.right} y={H - 6} text-anchor="end">{chartDay(t1, hours)}</text>
                {shown && <>
                    <line class="cross" x1={x(shown[0])} x2={x(shown[0])} y1={PAD.top} y2={H - PAD.bottom} />
                    <circle class="dot" cx={x(shown[0])} cy={y(shown[1])} r={4} />
                </>}
            </svg>
            {shown && (
                <div class="chart-tip" style={{ left: `${x(shown[0]) / W * 100}%`, top: `${y(shown[1]) / H * 100}%` }}>
                    {chartDay(shown[0], hours)}: <strong>{format(shown[1])}</strong>
                </div>
            )}
        </div>
    );
}

/** The week and year of what the hourly check has recorded. */
export function RangePicker({ range, setRange }: { range: "hourly" | "daily"; setRange: (range: "hourly" | "daily") => void }) {
    return (
        <div class="segmented" role="group" aria-label="Range">
            <button type="button" class={range === "hourly" ? "current" : ""} aria-pressed={range === "hourly"}
                    onClick={() => setRange("hourly")}>Week</button>
            <button type="button" class={range === "daily" ? "current" : ""} aria-pressed={range === "daily"}
                    onClick={() => setRange("daily")}>Year</button>
        </div>
    );
}

/** What to say until there are two points to join. */
export function NotEnoughHistory({ what, unit }: { what: string; unit: string }) {
    return (
        <p class="muted small">Not enough history to draw yet. The hourly check records {what} {unit};
            the chart fills in from then on.</p>
    );
}
