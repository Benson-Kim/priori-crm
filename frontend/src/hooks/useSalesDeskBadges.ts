/**
 * Counts for the Sales Desk nav badges.
 *
 * Companies and Future pipeline each carry a queue rather than a total:
 * profiles waiting to reach accounting, and prospects whose engage-by date has
 * arrived. A zero renders nothing rather than a "0".
 *
 * Keyed by nav path so the sidebar stays a declarative list and never has to
 * know what any particular count means.
 */

import { useCallback, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";

import {
    getCompanyList,
    getFuturePipelineSummary,
    subscribeToDeskChanges,
} from "@/services/salesDeskApi";

export type SalesDeskBadges = Record<string, number>;

export function useSalesDeskBadges(): SalesDeskBadges {
    const { pathname } = useLocation();
    const [badges, setBadges] = useState<SalesDeskBadges>({});

    const read = useCallback(() => {
        let active = true;

        Promise.all([getCompanyList(), getFuturePipelineSummary()])
            .then(([companies, future]) => {
                if (!active) return;
                setBadges({
                    "/sales-desk/companies": companies.filter((c) => c.needs_sync).length,
                    "/sales-desk/future-pipeline": future.due_count,
                });
            })
            .catch(() => {
                // A badge is decoration; failing to read one must never take
                // the navigation down with it.
                if (active) setBadges({});
            });

        return () => {
            active = false;
        };
    }, []);

    /*
     * Re-read on navigation, and again whenever desk data changes. Navigation
     * alone is not enough: clearing the needs-sync queue happens on the
     * companies workspace without leaving it, and the count beside that very
     * nav item would otherwise stay stale until the user navigated away and
     * back.
     */
    useEffect(read, [pathname, read]);

    useEffect(() => subscribeToDeskChanges(read), [read]);

    return badges;
}
