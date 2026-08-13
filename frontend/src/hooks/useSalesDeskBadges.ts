/**
 * Counts for the Sales Desk nav badges and the header bell.
 *
 * All of them come from ONE server payload (`GET /sales-desk/notifications`,
 * #45): the bell shows the total and the sidebar shows the groups it sums —
 * companies with profiles waiting to reach accounting, and future-pipeline
 * items whose date has arrived. Reading both from the same payload is what
 * keeps the bell from ever disagreeing with the badges it summarises.
 * A zero renders nothing rather than a "0".
 *
 * Badges are keyed by nav path so the sidebar stays a declarative list and
 * never has to know what any particular count means.
 */

import { useCallback, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";

import {
    getSalesDeskNotifications,
    subscribeToDeskChanges,
} from "@/services/salesDeskApi";

export type SalesDeskBadges = Record<string, number>;

export interface SalesDeskBadgeState {
    /** Sidebar badge counts, keyed by nav path. */
    badges: SalesDeskBadges;
    /** Header bell count: the server-computed sum of the badge groups. */
    notificationTotal: number;
}

const EMPTY: SalesDeskBadgeState = { badges: {}, notificationTotal: 0 };

export function useSalesDeskBadges(): SalesDeskBadgeState {
    const { pathname } = useLocation();
    const [state, setState] = useState<SalesDeskBadgeState>(EMPTY);

    const read = useCallback(() => {
        let active = true;

        getSalesDeskNotifications()
            .then((counts) => {
                if (!active) return;
                setState({
                    badges: {
                        "/sales-desk/companies": counts.companies_unsynced,
                        "/sales-desk/future-pipeline": counts.future_pipeline_due,
                    },
                    notificationTotal: counts.total,
                });
            })
            .catch(() => {
                // A badge is decoration; failing to read one must never take
                // the navigation down with it.
                if (active) setState(EMPTY);
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

    return state;
}
