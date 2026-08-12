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

import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";

import { getCompanyList, getFuturePipelineSummary } from "@/services/salesDeskApi";

export type SalesDeskBadges = Record<string, number>;

export function useSalesDeskBadges(): SalesDeskBadges {
    const { pathname } = useLocation();
    const [badges, setBadges] = useState<SalesDeskBadges>({});

    /*
     * Re-read on navigation. The desk's data lives in an in-memory store with
     * no change notifications, and every mutation happens on one of these
     * screens, so a route change is the cheapest honest refresh point.
     */
    useEffect(() => {
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
    }, [pathname]);

    return badges;
}
