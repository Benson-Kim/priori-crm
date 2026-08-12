/**
 * Sales Desk sidebar.
 *
 * A module-scoped rail. While the user is inside /sales-desk this replaces the
 * Business Central nav in `sidebar.tsx`, so the six desk destinations are the
 * only things competing for attention. The "Back to Priori" link is the way
 * out; without it the module would be a dead end.
 */

import { ArrowLeft } from "lucide-react";
import { Link, NavLink } from "react-router-dom";

import { useAuth } from "@/hooks/auth-context";
import { useSalesDeskBadges } from "@/hooks/useSalesDeskBadges";
import { cn } from "@/lib/utils";
import { salesDeskNavItems } from "./sales-desk-nav-items";

/** Initials for the footer avatar, falling back to the design's placeholder. */
function initialsOf(first?: string, last?: string): string {
    const initials = `${first?.[0] ?? ""}${last?.[0] ?? ""}`.toUpperCase();
    return initials || "FM";
}

export function SalesDeskSidebar() {
    const { user } = useAuth();
    const badges = useSalesDeskBadges();

    const fullName = user ? `${user.first_name} ${user.last_name}` : "Frank Mueke";
    const role = user?.role ? `${user.role[0].toUpperCase()}${user.role.slice(1)}` : "Senior Admin";

    return (
        <aside className="flex w-60 shrink-0 flex-col border-r border-desk-border bg-desk-surface">
            {/* Brand */}
            <div className="flex h-19 items-center px-4 pt-5 pb-4">
                <Link to="/sales-desk" className="flex items-center gap-2.5 px-2 py-1">
                    <img
                        src="/Logo Priori.svg"
                        alt="Priori Technologies"
                        className="h-8 w-auto shrink-0"
                    />
                    <span className="sr-only">Sales Desk</span>
                </Link>
            </div>

            {/* Module navigation */}
            <nav className="flex flex-1 flex-col gap-0.5 overflow-y-auto px-3 py-2">
                {salesDeskNavItems.map(({ path, label, icon: Icon, end }) => {
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
                                        ? "bg-desk-accent-soft text-priori-purple"
                                        : "text-desk-muted hover:bg-desk-bg hover:text-desk-ink"
                                )
                            }
                        >
                            <Icon className="size-4.5 shrink-0" aria-hidden="true" />
                            {label}
                            {count > 0 && (
                                <span className="ml-auto flex size-4 items-center justify-center rounded-full bg-priori-purple text-[10px] font-bold text-white">
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
                    className="flex items-center gap-2.5 rounded-xl px-3 py-2.5 text-[13px] font-medium text-desk-muted transition-colors hover:bg-desk-bg hover:text-desk-ink"
                >
                    <ArrowLeft className="size-4.5 shrink-0" aria-hidden="true" />
                    Back to Priori
                </Link>
            </div>

            {/* Signed-in user + build stamp */}
            <div className="border-t border-desk-border p-4">
                <div className="flex items-center gap-2.5">
                    <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-priori-purple text-xs font-bold text-white">
                        {initialsOf(user?.first_name, user?.last_name)}
                    </span>
                    <div className="min-w-0">
                        <p className="truncate text-xs font-semibold text-desk-ink">{fullName}</p>
                        <p className="truncate text-[10px] text-desk-muted">{role}</p>
                    </div>
                </div>
                <p className="pt-3 text-[10px] text-desk-muted">
                    &copy; 2026 Priori — All Rights Reserved
                </p>
                <p className="text-[10px] text-desk-muted">Version: 1.0.188-288</p>
            </div>
        </aside>
    );
}
