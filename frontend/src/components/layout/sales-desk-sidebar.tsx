/**
 * Sales Desk sidebar.
 *
 * A module-scoped rail. While the user is inside /sales-desk this replaces the
 * Business Central nav in `sidebar.tsx`, so the six desk destinations are the
 * only things competing for attention. The "Back to Business Central" link is
 * the way out; without it the module would be a dead end.
 */

import { ArrowLeft } from "lucide-react";
import { Link, NavLink } from "react-router-dom";

import { useMemo } from "react";

import { Avatar } from "@/components/ui/Avatar";
import { useAuth } from "@/hooks/auth-context";
import { useEnabledModules } from "@/hooks/useModuleAccess";
import type { SalesDeskBadges } from "@/hooks/useSalesDeskBadges";
import { cn } from "@/lib/utils";
import { visibleSalesDeskNavItems } from "./sales-desk-nav-items";

/** Initials for the footer avatar, falling back to the design's placeholder. */
export function SalesDeskSidebar({
    badges = {},
}: Readonly<{
    /**
     * Badge counts keyed by nav path — prop-driven from the layout so the
     * sidebar and the header bell read the same notifications payload and
     * can never disagree (#45/#52).
     */
    badges?: SalesDeskBadges;
}>) {
    const { user } = useAuth();

    /*
     * The desk nav honours the same per-owner module entitlements as the
     * Business Central sidebar (finding 07): a disabled module's entry is
     * hidden here rather than left to land the user on the backend's 403.
     */
    const { enabledModules } = useEnabledModules();
    const navItems = useMemo(
        () => visibleSalesDeskNavItems(enabledModules),
        [enabledModules]
    );

    const fullName = user ? `${user.first_name} ${user.last_name}` : "Frank Mueke";
    const role = user?.role ? `${user.role[0].toUpperCase()}${user.role.slice(1)}` : "Senior Admin";

    return (
        <aside className="flex w-60 shrink-0 flex-col border-r border-sd-border bg-sd-card">
            {/* Brand */}
            <div className="flex h-19 items-center px-4 pt-5 pb-4">
                <Link to="/sales-desk" className="flex items-center gap-2.5 px-2 py-1">
                    <img
                        src="/logo.svg"
                        alt="Business Central"
                        className="h-8 w-auto shrink-0"
                    />
                    <span className="sr-only">Sales Desk</span>
                </Link>
            </div>

            {/* Module navigation */}
            <nav className="flex flex-1 flex-col gap-0.5 overflow-y-auto px-3 py-2">
                {navItems.map(({ path, label, icon: Icon, end }) => {
                    const count = badges[path] ?? 0;
                    return (
                        <NavLink
                            key={path}
                            to={path}
                            end={end}
                            className={({ isActive }) =>
                                cn(
                                    "flex items-center gap-2.5 rounded-xl px-3 py-2.5 text-[13px] font-medium transition-colors",
                                    isActive
                                        ? "bg-sd-brand-bg text-sd-brand"
                                        : "text-sd-muted hover:bg-sd-surface hover:text-sd-ink"
                                )
                            }
                        >
                            <Icon className="size-4.5 shrink-0" aria-hidden="true" />
                            {label}
                            {count > 0 && (
                                <span className="ml-auto flex size-4 items-center justify-center rounded-full bg-sd-brand text-[10px] font-bold text-white">
                                    {count}
                                    <span className="sr-only"> needing attention</span>
                                </span>
                            )}
                        </NavLink>
                    );
                })}
            </nav>

            {/* Way back out of the module */}
            <div className="px-3 pb-2">
                <Link
                    to="/dashboard"
                    className="flex items-center gap-2.5 rounded-xl px-3 py-2.5 text-[13px] font-medium text-sd-muted transition-colors hover:bg-sd-surface hover:text-sd-ink"
                >
                    <ArrowLeft className="size-4.5 shrink-0" aria-hidden="true" />
                    Back to Business Central
                </Link>
            </div>

            {/* Signed-in user + build stamp */}
            <div className="border-t border-sd-border p-4">
                <div className="flex items-center gap-2.5">
                    <Avatar name={fullName} size={28} color="var(--color-sd-brand)" />
                    <div className="min-w-0">
                        <p className="truncate text-xs font-semibold text-sd-ink">{fullName}</p>
                        <p className="truncate text-[10px] text-sd-muted">{role}</p>
                    </div>
                </div>
                <p className="pt-3 text-[10px] text-sd-muted">
                    &copy; 2026 Business Central &mdash; All Rights Reserved
                </p>
                <p className="text-[10px] text-sd-muted">Version: 1.0.188-288</p>
            </div>
        </aside>
    );
}



