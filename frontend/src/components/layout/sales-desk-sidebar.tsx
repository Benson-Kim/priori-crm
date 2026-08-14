/**
 * Sales Desk sidebar.
 *
 * A module-scoped rail. While the user is inside /sales-desk this replaces the
 * Business Central nav in `sidebar.tsx`, so the six desk destinations are the
 * only things competing for attention. The "Back to Business Central" link is
 * the way out; without it the module would be a dead end.
 *
 * It is the same piece of furniture as the main rail and must look identical,
 * so every colour and metric comes from `sidebar-styles` rather than being
 * restated here.
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
import {
    BADGE,
    FOOTER,
    ICON,
    ITEM_ACTIVE,
    ITEM_BASE,
    ITEM_IDLE,
    NAV_SCROLL,
    RAIL,
} from "./sidebar-styles";

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

    const fullName = user ? `${user.first_name} ${user.last_name}` : "";
    const role = user?.role ?? "";

    return (
        // Same three rows as the main rail: brand, scrolling nav, pinned footer.
        <aside className={cn(RAIL, "w-64 px-4 py-6")}>
            {/* Brand — the same lockup the Business Central rail shows */}
            <Link
                to="/sales-desk"
                className="flex min-w-0 shrink-0 items-center gap-2.5 px-1"
            >
                <img
                    src="/logo.svg"
                    alt="Business Central logo"
                    className="h-8 w-8 shrink-0"
                />
                <div className="min-w-0 leading-tight">
                    <p className="truncate text-[13px] font-bold text-gray-900">
                        Business Central
                    </p>
                    <p className="truncate text-[10px] text-gray-600">Sales Desk</p>
                </div>
            </Link>

            {/* Module navigation */}
            <nav className={cn(NAV_SCROLL, "mt-8 flex flex-col gap-2")}>
                {navItems.map(({ path, label, icon: Icon, end }) => {
                    const count = badges[path] ?? 0;
                    return (
                        <NavLink
                            key={path}
                            to={path}
                            end={end}
                            className={({ isActive }) =>
                                cn(ITEM_BASE, isActive ? ITEM_ACTIVE : ITEM_IDLE)
                            }
                        >
                            <Icon className={ICON} aria-hidden="true" />
                            <span className="truncate">{label}</span>
                            {count > 0 && (
                                <span className={BADGE}>
                                    {count}
                                    <span className="sr-only"> needing attention</span>
                                </span>
                            )}
                        </NavLink>
                    );
                })}
            </nav>

            <div className="flex shrink-0 flex-col gap-1 pt-2">
                {/* Way back out of the module */}
                <Link to="/dashboard" className={cn(ITEM_BASE, ITEM_IDLE)}>
                    <ArrowLeft className={ICON} aria-hidden="true" />
                    Back to Business Central
                </Link>

                {/* Signed-in user + build stamp */}
                <div className={FOOTER}>
                    {user && (
                        <div className="flex items-center gap-2.5 px-1">
                            <Avatar
                                name={fullName}
                                size={28}
                                color="var(--color-priori-purple)"
                            />
                            <div className="min-w-0 leading-tight">
                                <p className="truncate text-xs font-semibold text-gray-900">
                                    {fullName}
                                </p>
                                <p className="truncate text-[10px] text-gray-600 capitalize">
                                    {role}
                                </p>
                            </div>
                        </div>
                    )}
                    <p className="px-1 text-[10px] leading-relaxed text-gray-600">
                        &copy; 2026 Business Central &middot; All Rights Reserved
                        <br />
                        Version: 1.0.188-288
                    </p>
                </div>
            </div>
        </aside>
    );
}
