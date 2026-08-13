/**
 * Sales Desk shell.
 *
 * A sibling of `DefaultLayout` rather than a nesting of it: the Sales Desk
 * is its own module with its own rail and a compact 64px header, so it swaps
 * the whole chrome instead of adding a nav group to the Business Central
 * sidebar. Both shells sit under the same `RequireAuth` guard.
 *
 * The header title is read from the matched route's `handle.header.title`,
 * the same convention `DefaultLayout` uses, so route definitions stay uniform
 * across the two shells.
 */

import { Bell } from "lucide-react";
import { Outlet, useMatches, type UIMatch } from "react-router-dom";

import { Avatar } from "@/components/ui/Avatar";
import { useAuth } from "@/hooks/auth-context";
import { useSalesDeskBadges } from "@/hooks/useSalesDeskBadges";
import type { RouteHandle } from "@/lib/types";
import { SalesDeskSidebar } from "./sales-desk-sidebar";

function SalesDeskHeader({
    title,
    description,
    notificationCount = 0,
}: Readonly<{ title: string; description?: string; notificationCount?: number }>) {
    const { user } = useAuth();
    const fullName = user ? `${user.first_name} ${user.last_name}` : "Frank Mueke";

    return (
        <header className="flex min-h-16 shrink-0 items-center justify-between gap-4 border-b border-sd-border bg-sd-card px-6 py-3">
            {/*
             * The page name and its description live here, as they do in every
             * other module. Screens do not repeat them in their own content.
             */}
            <div className="min-w-0">
                <h1 className="truncate text-base font-bold text-sd-ink">{title}</h1>
                {description && (
                    <p className="truncate text-xs text-sd-muted">{description}</p>
                )}
            </div>

            {/*
             * No global search here. Each screen searches its own records, so
             * a second box in the chrome would either duplicate that or do
             * nothing.
             */}
            <div className="flex items-center gap-3">
                <button
                    type="button"
                    aria-label={
                        notificationCount > 0
                            ? `Notifications (${notificationCount})`
                            : "Notifications"
                    }
                    className="relative flex size-8 items-center justify-center rounded-full border border-sd-border bg-sd-surface text-sd-muted transition-colors hover:text-sd-ink"
                >
                    <Bell className="size-3.5" aria-hidden="true" />
                    {/*
                     * The count is the server's notifications total (#45) —
                     * always the sum of the sidebar badge groups, so the bell
                     * and the badges can never disagree.
                     */}
                    {notificationCount > 0 && (
                        <span className="absolute -top-1 -right-1 flex size-4 items-center justify-center rounded-full bg-sd-brand text-[10px] font-bold text-white">
                            {notificationCount}
                        </span>
                    )}
                </button>

                <Avatar name={fullName} size={32} color="var(--color-sd-brand)" />
            </div>
        </header>
    );
}

export default function SalesDeskLayout() {
    const matches = useMatches() as UIMatch<unknown, RouteHandle>[];

    const header = [...matches].reverse().find((m) => m.handle?.header)?.handle?.header;
    const title = header?.title ?? "Sales Desk";

    /*
     * One notifications read feeds both the sidebar badges and the header
     * bell (#45: the bell total is the sum of the badge groups). Fetching
     * them separately is exactly how the two could drift apart.
     */
    const { badges, notificationTotal } = useSalesDeskBadges();

    /*
     * `sales-desk` scopes the module's control metrics and interaction
     * feedback (see index.css). The shared components keep their own sizing
     * everywhere else in the app.
     */
    return (
        <div className="sales-desk flex h-screen w-full overflow-hidden bg-sd-surface">
            <SalesDeskSidebar badges={badges} />
            <div className="flex min-w-0 flex-1 flex-col">
                <SalesDeskHeader
                    title={title}
                    description={header?.description}
                    notificationCount={notificationTotal}
                />
                {/*
                 * `relative` so absolutely positioned descendants (the
                 * visually-hidden inputs behind styled checkboxes, say) resolve
                 * against this scroller rather than the document, which would
                 * otherwise let them stretch the page and scroll the shell.
                 */}
                <main className="relative flex-1 overflow-auto p-6">
                    <Outlet />
                </main>
            </div>
        </div>
    );
}
