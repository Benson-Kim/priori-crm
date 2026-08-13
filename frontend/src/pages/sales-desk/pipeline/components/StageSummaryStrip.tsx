/**
 * StageSummaryStrip
 *
 * Where the book sits, stage by stage. The closed column reports a win rate
 * rather than a value, because closed money is no longer pipeline. The "avg
 * Nd" line is the mean age of deals parked at that stage, which exposes a
 * stage that has become a waiting room.
 */

import { formatDeskMoneyCompact, type PipelineOverview } from "@/services/salesDeskApi";

interface StageSummaryStripProps {
    overview: PipelineOverview;
}

function Column({
    label,
    headline,
    caption,
    isLast,
}: Readonly<{ label: string; headline: string; caption: string; isLast?: boolean }>) {
    return (
        <div className={isLast ? "px-4 py-3" : "border-sd-border px-4 py-3 lg:border-r"}>
            <p className="text-[10px] font-bold tracking-[0.25px] text-sd-muted uppercase">
                {label}
            </p>
            <p className="pt-1 text-[13px] font-bold text-sd-ink">{headline}</p>
            <p className="pt-0.5 text-[11px] text-sd-muted">{caption}</p>
        </div>
    );
}

export function StageSummaryStrip({ overview }: Readonly<StageSummaryStripProps>) {
    const { stages, closed, currency } = overview;

    return (
        <div className="grid grid-cols-1 divide-y divide-sd-border rounded-2xl border border-sd-border bg-sd-card sm:grid-cols-2 sm:divide-y-0 lg:grid-cols-5">
            {stages.map((column) => (
                <Column
                    key={column.stage}
                    label={column.stage}
                    headline={`${column.deal_count} · ${formatDeskMoneyCompact(
                        column.value,
                        currency
                    )}`}
                    caption={
                        column.deal_count
                            ? `avg ${column.average_age_days}d in stage`
                            : "no deals here"
                    }
                />
            ))}
            <Column
                isLast
                label="Closed"
                headline={
                    closed.win_rate === null
                        ? `${closed.deal_count} · —`
                        : `${closed.deal_count} · ${Math.round(closed.win_rate * 100)}% won`
                }
                caption={`${closed.won_count} won · ${closed.lost_count} lost`}
            />
        </div>
    );
}
