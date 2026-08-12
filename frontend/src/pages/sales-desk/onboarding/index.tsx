/**
 * Onboarding, post-sale delivery checklists.
 *
 * One card per won deal, tracking the same seven steps every time. The fixed
 * list is what lets one delivery be compared against another. Ordered least
 * complete first, so whatever needs pushing leads the page.
 */

import { useCallback, useEffect, useState } from "react";

import { ChecklistRow } from "@/components/ui/ChecklistRow";
import { LoadingState } from "@/components/ui/LoadingState";
import { ProgressBar } from "@/components/ui/ProgressBar";
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
            className="overflow-hidden rounded-2xl border border-sd-border bg-sd-card shadow-[0_1px_3px_rgba(0,0,0,0.06)]"
        >
            <div className="p-5">
                <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                        <h2 id={headingId} className="text-[15px] font-bold text-sd-ink">
                            {onboarding.company_name}
                        </h2>
                        <p className="pt-0.5 text-[13px] text-sd-muted">{onboarding.plan}</p>
                    </div>
                    <span className="shrink-0 rounded-full bg-sd-brand-bg px-2.5 py-0.5 text-xs font-semibold text-priori-purple">
                        {percent}%
                    </span>
                </div>

                <ProgressBar
                    percent={percent}
                    className="mt-4"
                    aria-label={`${onboarding.company_name} delivery progress`}
                />

                <p className="pt-2 text-[13px] text-sd-muted">
                    {onboarding.completed_count} of {onboarding.total_count} tasks complete
                </p>
            </div>

            <ul className="border-t border-sd-border">
                {onboarding.tasks.map((task) => (
                    <ChecklistRow
                        key={task.index}
                        id={`${headingId}-task-${task.index}`}
                        label={task.label}
                        done={task.completed}
                        step={task.index + 1}
                        disabled={isSaving}
                        onToggle={(done) => onToggle(task.index, done)}
                    />
                ))}
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
                <p className="rounded-2xl border border-sd-border bg-sd-card p-10 text-center text-[13px] text-sd-muted">
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
