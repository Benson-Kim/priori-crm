/**
 * BookingsTrendChart
 *
 * Closed bookings over the trailing 12 months. Won grows up from the baseline
 * and lost grows down, so a month's net outcome reads at a glance. Lost is
 * plotted negative purely to place it below the axis; the tooltip shows the
 * magnitude.
 */

import {
    Bar,
    BarChart,
    ReferenceLine,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";

import { formatDeskMoney, type BillingCurrency, type BookingsPoint } from "@/services/salesDeskApi";

const WON_COLOR = "#0f7a38";
const LOST_COLOR = "#c23434";

interface ChartDatum {
    month: string;
    won: number;
    /** Negative so the bar renders below the baseline. */
    lost: number;
    lostMagnitude: number;
}

interface TooltipProps {
    active?: boolean;
    payload?: Array<{ payload: ChartDatum }>;
    currency: BillingCurrency;
}

function BookingsTooltip({ active, payload, currency }: Readonly<TooltipProps>) {
    if (!active || !payload?.length) return null;
    const { month, won, lostMagnitude } = payload[0].payload;

    return (
        <div className="rounded-xl border border-sd-border bg-sd-card px-3 py-2 text-xs shadow-[0_4px_15px_rgba(0,0,0,0.08)]">
            <p className="font-semibold text-sd-ink">{month}</p>
            <p className="text-sd-success">Won {formatDeskMoney(won, currency)}</p>
            <p className="text-sd-danger">Lost {formatDeskMoney(lostMagnitude, currency)}</p>
        </div>
    );
}

interface BookingsTrendChartProps {
    data: BookingsPoint[];
    currency: BillingCurrency;
}

export function BookingsTrendChart({ data, currency }: Readonly<BookingsTrendChartProps>) {
    const chartData: ChartDatum[] = data.map(({ month, won, lost }) => ({
        month,
        won,
        lost: -lost,
        lostMagnitude: lost,
    }));

    return (
        <div>
            <ResponsiveContainer width="100%" height={176}>
                <BarChart
                    data={chartData}
                    margin={{ top: 4, right: 0, bottom: 0, left: 0 }}
                    stackOffset="sign"
                >
                    <XAxis
                        dataKey="month"
                        axisLine={false}
                        tickLine={false}
                        tick={{ fill: "#6b7688", fontSize: 9 }}
                    />
                    <YAxis hide domain={["dataMin", "dataMax"]} />
                    <ReferenceLine y={0} stroke="#e4e8ef" />
                    <Tooltip
                        content={<BookingsTooltip currency={currency} />}
                        cursor={{ fill: "rgba(145,43,144,0.04)" }}
                    />
                    {/*
                     * A shared stackId with stackOffset="sign" keeps won and
                     * lost in one column, stacking the positive value up from
                     * zero and the negative one down. Without it the two
                     * series sit side by side.
                     */}
                    <Bar
                        dataKey="won"
                        stackId="bookings"
                        fill={WON_COLOR}
                        radius={[4, 4, 0, 0]}
                        barSize={18}
                    />
                    <Bar
                        dataKey="lost"
                        stackId="bookings"
                        fill={LOST_COLOR}
                        fillOpacity={0.45}
                        radius={[0, 0, 4, 4]}
                        barSize={18}
                    />
                </BarChart>
            </ResponsiveContainer>

            <div className="flex items-center gap-4 pt-3">
                <span className="flex items-center gap-1.5 text-xs text-sd-muted">
                    <span className="size-2.5 rounded-[3px] bg-sd-success" aria-hidden="true" />
                    Won
                </span>
                <span className="flex items-center gap-1.5 text-xs text-sd-muted">
                    <span
                        className="size-2.5 rounded-[3px] bg-sd-danger opacity-45"
                        aria-hidden="true"
                    />
                    Lost
                </span>
            </div>
        </div>
    );
}
