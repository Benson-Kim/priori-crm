/**
 * RepFilterCards
 *
 * The pipeline's owner selector, doubling as a scoreboard. Selecting a card
 * scopes the whole page, so the stage strip, filter counts and table follow.
 */

import { cn } from "@/lib/utils";
import {
    formatDeskMoneyCompact,
    type BillingCurrency,
    type PipelineOverview,
} from "@/services/salesDeskApi";
import { RepAvatar } from "../../components/RepAvatar";

/** "2 open · $14.6k · 50% won", the parts that apply, joined. */
function subtitleOf(
    openCount: number,
    openValue: number,
    winRate: number | null,
    currency: BillingCurrency
): string {
    const parts = [
        `${openCount} open`,
        formatDeskMoneyCompact(openValue, currency),
    ];
    if (winRate !== null) parts.push(`${Math.round(winRate * 100)}% won`);
    return parts.join(" · ");
}

interface RepFilterCardsProps {
    overview: PipelineOverview;
    /** Currently scoped rep, or null for the whole team. */
    selectedOwnerId: string | null;
    onSelect: (ownerId: string | null) => void;
}

export function RepFilterCards({
    overview,
    selectedOwnerId,
    onSelect,
}: Readonly<RepFilterCardsProps>) {
    const { reps, team, currency } = overview;

    const cardClass = (isSelected: boolean) =>
        cn(
            "flex items-center gap-3 rounded-2xl border p-3 text-left transition-colors",
            isSelected
                ? "border-desk-ink bg-desk-ink"
                : "border-desk-border bg-desk-surface hover:border-desk-muted"
        );

    return (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {reps.map((rep) => {
                const isSelected = selectedOwnerId === rep.id;
                return (
                    <button
                        key={rep.id}
                        type="button"
                        onClick={() => onSelect(rep.id)}
                        aria-pressed={isSelected}
                        className={cardClass(isSelected)}
                    >
                        <RepAvatar initials={rep.initials} color={rep.color} size="lg" />
                        <div className="min-w-0 flex-1">
                            <p
                                className={cn(
                                    "truncate text-xs font-semibold",
                                    isSelected ? "text-white" : "text-desk-ink"
                                )}
                            >
                                {rep.name}
                            </p>
                            <p
                                className={cn(
                                    "truncate text-[11px]",
                                    isSelected ? "text-white/70" : "text-desk-muted"
                                )}
                            >
                                {subtitleOf(
                                    rep.open_deal_count,
                                    rep.open_value,
                                    rep.win_rate,
                                    currency
                                )}
                            </p>
                        </div>
                        {rep.cold_deal_count > 0 && (
                            <span className="shrink-0 rounded bg-desk-warm-soft px-1.5 py-0.5 text-[11px] font-semibold text-desk-warm">
                                {rep.cold_deal_count} cold
                            </span>
                        )}
                    </button>
                );
            })}

            <button
                type="button"
                onClick={() => onSelect(null)}
                aria-pressed={selectedOwnerId === null}
                className={cardClass(selectedOwnerId === null)}
            >
                <span
                    className={cn(
                        "flex size-8.5 shrink-0 items-center justify-center rounded-full text-xs font-bold",
                        selectedOwnerId === null
                            ? "bg-white/20 text-white"
                            : "bg-desk-neutral-soft text-desk-muted"
                    )}
                    aria-hidden="true"
                >
                    ALL
                </span>
                <div className="min-w-0 flex-1">
                    <p
                        className={cn(
                            "truncate text-xs font-semibold",
                            selectedOwnerId === null ? "text-white" : "text-desk-ink"
                        )}
                    >
                        Whole team
                    </p>
                    <p
                        className={cn(
                            "truncate text-[11px]",
                            selectedOwnerId === null ? "text-white/70" : "text-desk-muted"
                        )}
                    >
                        {`${team.open_deal_count} open · ${formatDeskMoneyCompact(
                            team.open_value,
                            currency
                        )}`}
                    </p>
                </div>
            </button>
        </div>
    );
}
