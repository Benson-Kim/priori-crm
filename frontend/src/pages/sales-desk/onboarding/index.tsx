/**
 * Onboarding, post-sale delivery checklists.
 *
 * One card per won deal, tracking the same seven steps every time. The fixed
 * list is what lets one delivery be compared against another. Ordered least
 * complete first, so whatever needs pushing leads the page.
 */

import { Check } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { LoadingState } from "@/components/ui/LoadingState";
import { cn } from "@/lib/utils";
import {
    getOnboardingList,
    setOnboardingTask,
    type OnboardingRow,
} from "@/services/salesDeskApi";

interface ChecklistCardProps {
    onboarding: OnboardingRow;
    onToggle: (taskIndex: number, completed: boolean) => void;
    isSaving: boolean;
}

function ChecklistCard({ onboarding, onToggle, isSaving }: Readonly<ChecklistCardProps>) {
    const percent = Math.round(onboarding.progress * 100);
    const headingId = `onboarding-${onboarding.id}`;

    return (
        <section
            aria-labelledby={headingId}
            className="overflow-hidden rounded-2xl border border-desk-border bg-desk-surface shadow-[0_1px_3px_rgba(0,0,0,0.06)]"
        >
            <div className="p-5">
                <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                        <h2 id={headingId} className="text-[15px] font-bold text-desk-ink">
                            {onboarding.company_name}
                        </h2>
                        <p className="pt-0.5 text-[13px] text-desk-muted">{onboarding.plan}</p>
                    </div>
                    <span className="shrink-0 rounded-full bg-desk-accent-soft px-2.5 py-0.5 text-xs font-semibold text-priori-purple">
                        {percent}%
                    </span>
                </div>

                <div
                    className="mt-4 h-1.5 w-full overflow-hidden rounded-full bg-desk-border"
                    role="progressbar"
                    aria-label={`${onboarding.company_name} delivery progress`}
                    aria-valuenow={percent}
                    aria-valuemin={0}
                    aria-valuemax={100}
                >
                    <div
                        className="h-full rounded-full bg-priori-purple transition-[width] duration-300"
                        style={{ width: `${percent}%` }}
                    />
                </div>

                <p className="pt-2 text-[13px] text-desk-muted">
                    {onboarding.completed_count} of {onboarding.total_count} tasks complete
                </p>
            </div>

            <ul className="border-t border-desk-border">
                {onboarding.tasks.map((task) => {
                    const inputId = `${headingId}-task-${task.index}`;
                    return (
                        <li
                            key={task.index}
                            className="flex items-center gap-3 border-b border-desk-border px-5 py-3 last:border-b-0"
                        >
                            {/*
                             * A real checkbox, visually hidden and driven by
                             * the styled box beside it, so the row keeps
                             * native keyboard and screen-reader behaviour.
                             */}
                            <input
                                type="checkbox"
                                id={inputId}
                                className="peer sr-only"
                                checked={task.completed}
                                disabled={isSaving}
                                onChange={(event) => onToggle(task.index, event.target.checked)}
                            />
                            <label
                                htmlFor={inputId}
                                className="flex flex-1 cursor-pointer items-center gap-3 select-none"
                            >
                                <span
                                    className={cn(
                                        "flex size-5 shrink-0 items-center justify-center rounded border transition-colors",
                                        task.completed
                                            ? "border-priori-purple bg-priori-purple"
                                            : "border-gray-300 bg-desk-surface",
                                        "peer-focus-visible:ring-2 peer-focus-visible:ring-priori-purple/40"
                                    )}
                                    aria-hidden="true"
                                >
                                    {task.completed && (
                                        <Check className="size-3.5 text-white" strokeWidth={3} />
                                    )}
                                </span>
                                <span
                                    className={cn(
                                        "flex-1 text-[13px]",
                                        task.completed
                                            ? "text-desk-muted line-through"
                                            : "text-desk-ink"
                                    )}
                                >
                                    {task.label}
                                </span>
                                <span className="shrink-0 text-xs text-desk-muted">
                                    Step {task.index + 1}
                                </span>
                            </label>
                        </li>
                    );
                })}
            </ul>
        </section>
    );
}

export default function SalesDeskOnboardingPage() {
    const [onboardings, setOnboardings] = useState<OnboardingRow[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [isSaving, setIsSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let active = true;

        getOnboardingList()
            .then((rows) => {
                if (active) {
                    setOnboardings(rows);
                    setError(null);
                }
            })
            .catch((err) => {
                if (active)
                    setError(
                        err instanceof Error ? err.message : "Failed to load onboarding"
                    );
            })
            .finally(() => {
                if (active) setIsLoading(false);
            });

        return () => {
            active = false;
        };
    }, []);

    /*
     * Ticking a step updates that card in place rather than re-reading the
     * page, since re-sorting mid-checklist would move the next row out from
     * under the cursor.
     */
    const toggle = useCallback(
        async (onboardingId: number, taskIndex: number, completed: boolean) => {
            setIsSaving(true);
            try {
                const updated = await setOnboardingTask(onboardingId, taskIndex, completed);
                setOnboardings((previous) =>
                    previous.map((row) => (row.id === updated.id ? updated : row))
                );
                setError(null);
            } catch (err) {
                setError(err instanceof Error ? err.message : "Could not update that step.");
            } finally {
                setIsSaving(false);
            }
        },
        []
    );

    return (
        <div className="flex flex-col gap-5">
            <h1 className="text-2xl leading-8 font-bold text-desk-ink">Onboarding</h1>

            {error && (
                <div
                    role="alert"
                    className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700"
                >
                    {error}
                </div>
            )}

            {isLoading ? (
                <LoadingState message="Loading onboarding..." className="h-64" />
            ) : onboardings.length === 0 ? (
                <p className="rounded-2xl border border-desk-border bg-desk-surface p-10 text-center text-[13px] text-desk-muted">
                    Nothing in delivery.
                </p>
            ) : (
                <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
                    {onboardings.map((onboarding) => (
                        <ChecklistCard
                            key={onboarding.id}
                            onboarding={onboarding}
                            isSaving={isSaving}
                            onToggle={(taskIndex, completed) =>
                                void toggle(onboarding.id, taskIndex, completed)
                            }
                        />
                    ))}
                </div>
            )}
        </div>
    );
}
