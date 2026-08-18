import { useAuth, useCanEditOwner } from "@/hooks/auth-context";
import { useEnabledModules } from "@/hooks/useModuleAccess";
import { cn } from "@/lib/utils";
import {
    ChevronDown,
    ChevronRight,
    HelpCircle,
    LogOut,
    PanelLeftClose,
    PanelLeftOpen,
    Settings,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { Avatar } from "@/components/ui/Avatar";
import { visibleNavItems, type NavChild } from "./nav-items";
import {
    BADGE,
    FOOTER,
    GROUP_ACTIVE,
    ICON,
    ITEM_ACTIVE,
    ITEM_BASE,
    ITEM_IDLE,
    NAV_SCROLL,
    RAIL,
} from "./sidebar-styles";

/** localStorage key persisting the collapsed/expanded preference. */
const COLLAPSED_STORAGE_KEY = "sidebar:collapsed";

/**
 * Server-driven nav badge counts keyed by `NavChild.badgeKey` (issue #45's
 * notifications endpoint, never computed client-side). Undefined/zero
 * entries render no badge, so the sidebar stays clean until #45 lands.
 */
export type NavBadgeCounts = Partial<Record<string, number>>;

interface SidebarProps {
    badgeCounts?: NavBadgeCounts;
}

/** 16px brand circle, white bold 10 (e.g. Companies 2, Future pipeline 2). */
function NavCountBadge({ count }: Readonly<{ count?: number }>) {
    if (!count || count <= 0) return null;
    return <span className={BADGE}>{count}</span>;
}

function readInitialCollapsed(): boolean {
    if (typeof window === "undefined") return false;
    try {
        return window.localStorage.getItem(COLLAPSED_STORAGE_KEY) === "true";
    } catch {
        // localStorage may be unavailable (private mode / SSR) — default expanded.
        return false;
    }
}

export function Sidebar({ badgeCounts }: Readonly<SidebarProps>) {
    const { pathname } = useLocation();
    const navigate = useNavigate();
    const { logout, user } = useAuth();

    // Per-owner module entitlements: hide nav entries for disabled modules
    // (fail-open while the bootstrap loads). Groups whose children are all
    // hidden disappear entirely.
    const { enabledModules } = useEnabledModules();
    const items = useMemo(
        () => visibleNavItems(enabledModules),
        [enabledModules]
    );

    // Settings visibility mirrors the backend owner-profile write roles
    // (MANAGER/ADMIN); the Modules tab inside is additionally admin-only.
    const canEditOwner = useCanEditOwner();

    const isActive = (path: string) =>
        pathname === path || pathname.startsWith(path + "/");

    const isGroupActive = (children?: NavChild[]) =>
        children?.some((child) => isActive(child.path));

    const [manualOpen, setManualOpen] = useState<Record<string, boolean>>({});
    const [collapsed, setCollapsed] = useState<boolean>(readInitialCollapsed);

    // Persist the collapsed preference so it survives reloads.
    useEffect(() => {
        try {
            window.localStorage.setItem(
                COLLAPSED_STORAGE_KEY,
                collapsed ? "true" : "false"
            );
        } catch {
            // Ignore persistence failures (private mode / quota).
        }
    }, [collapsed]);

    const toggleGroup = (label: string) => {
        setManualOpen((prev) => ({
            ...prev,
            [label]: !prev[label],
        }));
    };

    return (
        // Three rows: brand, a nav that scrolls on its own, then the footer.
        // The nav is the only thing that grows, so the user card and build
        // stamp stay on screen however long the nav gets.
        <aside
            className={cn(
                RAIL,
                "py-6",
                collapsed ? "w-20 px-2" : "w-64 px-4"
            )}
        >
                    {/* Logo + collapse toggle */}
                    <div
                        className={cn(
                            "flex shrink-0 items-center",
                            collapsed ? "justify-center" : "justify-between gap-2"
                        )}
                    >
                        {!collapsed && (
                            <div className="flex min-w-0 items-center gap-2.5 px-1">
                                <img
                                    src="/logo.svg"
                                    alt="Business Central logo"
                                    className="h-8 w-8 shrink-0"
                                />
                                <div className="min-w-0 leading-tight">
                                    <p className="truncate text-dense-md font-bold text-gray-900">
                                        Business Central
                                    </p>
                                    <p className="truncate text-dense-xs text-gray-600">
                                        Sales &amp; Accounting
                                    </p>
                                </div>
                            </div>
                        )}
                        <button
                            type="button"
                            onClick={() => setCollapsed((prev) => !prev)}
                            aria-expanded={!collapsed}
                            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
                            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
                            className="p-2 rounded-lg text-gray-600 hover:bg-white/60 transition-colors cursor-pointer shrink-0"
                        >
                            {collapsed ? (
                                <PanelLeftOpen size={20} />
                            ) : (
                                <PanelLeftClose size={20} />
                            )}
                        </button>
                    </div>

                    <nav className={cn(NAV_SCROLL, "mt-8 flex flex-col gap-2")}>
                        {items.map((item) => {
                            const Icon = item.icon;

                            if (!item.children) {
                                return (
                                    <Link
                                        key={item.path}
                                        to={item.path}
                                        aria-label={item.label}
                                        title={collapsed ? item.label : undefined}
                                        className={cn(
                                            ITEM_BASE,
                                            collapsed && "justify-center px-0",
                                            !collapsed && item.leavesShell && "justify-between",
                                            isActive(item.path) ? ITEM_ACTIVE : ITEM_IDLE
                                        )}
                                    >
                                        <div
                                            className={cn(
                                                "flex items-center",
                                                !collapsed && "gap-3"
                                            )}
                                        >
                                            {Icon && <Icon className={ICON} />}
                                            {!collapsed && item.label}
                                        </div>

                                        {/* Right chevron reads as "leaves this
                                            shell", against the groups' down
                                            chevron meaning "opens here". */}
                                        {!collapsed && item.leavesShell && (
                                            <ChevronRight size={16} />
                                        )}
                                    </Link>
                                );
                            }

                            const open =
                                manualOpen[item.label] ?? isGroupActive(item.children);

                            return (
                                <div key={item.label} className="flex flex-col">
                                    {/* GROUP HEADER */}
                                    <button
                                        type="button"
                                        onClick={() => toggleGroup(item.label)}
                                        aria-expanded={open}
                                        aria-label={item.label}
                                        title={collapsed ? item.label : undefined}
                                        className={cn(
                                            ITEM_BASE,
                                            "cursor-pointer",
                                            collapsed
                                                ? "justify-center px-0"
                                                : "justify-between",
                                            isGroupActive(item.children)
                                                ? GROUP_ACTIVE
                                                : ITEM_IDLE
                                        )}
                                    >
                                        <div
                                            className={cn(
                                                "flex items-center",
                                                !collapsed && "gap-3"
                                            )}
                                        >
                                            {Icon && <Icon className={ICON} />}
                                            {!collapsed && item.label}
                                        </div>

                                        {!collapsed && (
                                            <ChevronDown
                                                size={16}
                                                className={cn(
                                                    "transition-transform duration-200",
                                                    open && "rotate-180"
                                                )}
                                            />
                                        )}
                                    </button>

                                    {/* CHILDREN */}
                                    <div
                                        className={cn(
                                            "flex flex-col gap-1 mt-1 overflow-hidden transition-all duration-200",
                                            collapsed ? "pr-0" : "pr-2",
                                            open
                                                ? "max-h-96 opacity-100"
                                                : "max-h-0 opacity-0 pointer-events-none"
                                        )}
                                    >
                                        {item.children.map((child) => {
                                            const ChildIcon = child.icon;

                                            return (
                                                <Link
                                                    key={child.path}
                                                    to={child.path}
                                                    aria-label={child.label}
                                                    title={collapsed ? child.label : undefined}
                                                    className={cn(
                                                        ITEM_BASE,
                                                        collapsed
                                                            ? "justify-center px-1 py-2"
                                                            : "pl-6",
                                                        isActive(child.path)
                                                            ? ITEM_ACTIVE
                                                            : ITEM_IDLE
                                                    )}
                                                >
                                                    {ChildIcon && (
                                                        <ChildIcon
                                                            className={ICON}
                                                            aria-hidden="true"
                                                        />
                                                    )}

                                                    {!collapsed && (
                                                        <span className="truncate">{child.label}</span>
                                                    )}

                                                    {!collapsed && child.badgeKey && (
                                                        <NavCountBadge
                                                            count={badgeCounts?.[child.badgeKey]}
                                                        />
                                                    )}
                                                </Link>
                                            );
                                        })}
                                    </div>
                                </div>
                            );
                        })}
                    </nav>

                <div className="flex shrink-0 flex-col gap-1 pt-2">
                    {canEditOwner && (
                        <Link
                            to="/settings"
                            aria-label="Settings"
                            title={collapsed ? "Settings" : undefined}
                            className={cn(
                                ITEM_BASE,
                                collapsed && "justify-center px-0",
                                isActive("/settings")
                                    ? ITEM_ACTIVE
                                    : ITEM_IDLE
                            )}
                        >
                            <Settings className={ICON} />
                            {!collapsed && "Settings"}
                        </Link>
                    )}
                    <Link
                        to="help"
                        aria-label="Help"
                        title={collapsed ? "Help" : undefined}
                        className={cn(
                            ITEM_BASE,
                            collapsed && "justify-center px-0",
                            isActive("help") ? ITEM_ACTIVE : ITEM_IDLE
                        )}
                    >
                        <HelpCircle className={ICON} />
                        {!collapsed && "Help"}
                    </Link>

                    <button
                        type="button"
                        onClick={() => {
                            logout();
                            navigate("/login", { replace: true });
                        }}
                        aria-label="Logout"
                        title={collapsed ? "Logout" : undefined}
                        className={cn(
                            ITEM_BASE,
                            ITEM_IDLE,
                            "cursor-pointer",
                            collapsed && "justify-center px-0"
                        )}
                    >
                        <LogOut className={ICON} />
                        {!collapsed && "Logout"}
                    </button>
                    {!collapsed && (
                        <div className={FOOTER}>
                            {user && (
                                <div className="flex items-center gap-2.5 px-1">
                                    <Avatar
                                        name={`${user.first_name} ${user.last_name}`}
                                        size={28}
                                        color="var(--color-priori-purple)"
                                    />
                                    <div className="min-w-0 leading-tight">
                                        <p className="truncate text-xs font-semibold text-gray-900">
                                            {user.first_name} {user.last_name}
                                        </p>
                                        <p className="truncate text-dense-xs text-gray-600 capitalize">
                                            {user.role}
                                        </p>
                                    </div>
                                </div>
                            )}
                            <p className="px-1 text-dense-xs leading-relaxed text-gray-600">
                                &copy; 2026 Business Central &middot; All Rights Reserved
                                <br />
                                Version: 1.0.188-288
                            </p>
                        </div>
                    )}
                </div>
        </aside>
    );
}
