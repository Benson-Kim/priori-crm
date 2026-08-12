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

import { Bell, Search } from "lucide-react";
import { Outlet, useMatches, type UIMatch } from "react-router-dom";

import { useAuth } from "@/hooks/auth-context";
import type { RouteHandle } from "@/lib/types";
import { SalesDeskSidebar } from "./sales-desk-sidebar";

function SalesDeskHeader({ title }: Readonly<{ title: string }>) {
    const { user } = useAuth();
    const initials =
        `${user?.first_name?.[0] ?? ""}${user?.last_name?.[0] ?? ""}`.toUpperCase() || "FM";

    return (
        <header className="flex h-16 shrink-0 items-center justify-between border-b border-sd-border bg-sd-card px-6">
            <h1 className="text-[13px] font-semibold text-sd-ink">{title}</h1>

            <div className="flex items-center gap-3">
                {/*
                 * Cross-module search is inert until there is a record set to
                 * filter, so it stays disabled rather than silently swallowing
                 * input.
                 */}
                <div className="hidden items-center gap-2 rounded-xl border border-sd-border bg-sd-surface px-3 py-1.5 sm:flex">
                    <Search className="size-3.5 shrink-0 text-sd-muted" aria-hidden="true" />
                    <input
                        type="search"
                        disabled
                        aria-label="Search companies and deals"
                        title="Search arrives with the Pipeline and Companies screens"
                        placeholder="Search companies, deals…"
                        className="w-40 bg-transparent text-xs text-sd-ink placeholder:text-sd-muted focus:outline-none"
                    />
                </div>

                <button
                    type="button"
                    aria-label="Notifications"
                    className="flex size-8 items-center justify-center rounded-full border border-sd-border bg-sd-surface text-sd-muted transition-colors hover:text-sd-ink"
                >
                    <Bell className="size-3.5" aria-hidden="true" />
                </button>

                <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-priori-purple text-xs font-bold text-white">
                    {initials}
                </span>
            </div>
        </header>
    );
}

export default function SalesDeskLayout() {
    const matches = useMatches() as UIMatch<unknown, RouteHandle>[];

    const title =
        [...matches].reverse().find((m) => m.handle?.header)?.handle?.header?.title ??
        "Sales Desk";

    return (
        <div className="flex h-screen w-full overflow-hidden bg-sd-surface">
            <SalesDeskSidebar />
            <div className="flex min-w-0 flex-1 flex-col">
                <SalesDeskHeader title={title} />
                <main className="flex-1 overflow-auto p-6">
                    <Outlet />
                </main>
            </div>
        </div>
    );
}
