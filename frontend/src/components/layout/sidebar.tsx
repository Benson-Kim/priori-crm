import { useAuth } from "@/hooks/auth-context";
import { useEnabledModules } from "@/hooks/useModuleAccess";
import { cn } from "@/lib/utils";
import {
    ChevronDown,
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
    return (
        <span className="ml-auto flex h-4 min-w-4 shrink-0 items-center justify-center rounded-full bg-sd-brand px-1 text-[10px] font-bold text-white">
            {count}
        </span>
    );
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

    const isAdmin = user?.role?.toLowerCase() === "admin";

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
        <aside
            className={cn(
                "bg-white border-r border-sd-border flex flex-col min-h-screen py-6 transition-all duration-200",
                collapsed ? "w-20 px-2" : "w-60 px-3"
            )}
        >
            <div className="flex flex-col justify-between h-screen">
                <div className="flex flex-col gap-8">
                    {/* Logo + collapse toggle */}
                    <div
                        className={cn(
                            "flex items-center",
                            collapsed ? "justify-center" : "justify-between gap-2"
                        )}
                    >
                        {!collapsed && (
                            <div className="flex min-w-0 items-center gap-2.5 px-1">
                                <img
                                    src="/Logo Priori.svg"
                                    alt="Business Central logo"
                                    className="h-8 w-8 shrink-0"
                                />
                                <div className="min-w-0 leading-tight">
                                    <p className="truncate text-[13px] font-bold text-sd-ink">
                                        Business Central
                                    </p>
                                    <p className="truncate text-[10px] text-sd-muted">
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

                    <nav className="flex flex-col gap-2">
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
                                            "flex items-center gap-3 px-3 py-2.5 rounded-xl text-[13px] font-medium transition-colors",
                                            collapsed && "justify-center px-0",
                                            isActive(item.path)
                                                ? "bg-sd-brand-bg text-sd-brand"
                                                : "text-sd-muted hover:bg-sd-surface"
                                        )}
                                    >
                                        {Icon && <Icon size={18} />}
                                        {!collapsed && item.label}
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
                                            "flex items-center px-3 py-2.5 text-[13px] font-medium rounded-xl transition-colors cursor-pointer",
                                            collapsed
                                                ? "justify-center px-0"
                                                : "justify-between",
                                            isGroupActive(item.children)
                                                ? "text-sd-brand font-semibold"
                                                : "text-sd-muted hover:bg-sd-surface"
                                        )}
                                    >
                                        <div
                                            className={cn(
                                                "flex items-center",
                                                !collapsed && "gap-3"
                                            )}
                                        >
                                            {Icon && <Icon size={18} />}
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
                                                        "flex items-center gap-3 rounded-xl text-[13px] font-medium transition-all",
                                                        collapsed
                                                            ? "justify-center px-1 py-2"
                                                            : "px-3 py-2.5 pl-6",
                                                        isActive(child.path)
                                                            ? "bg-sd-brand-bg text-sd-brand font-semibold"
                                                            : "text-sd-muted hover:bg-sd-surface"
                                                    )}
                                                >
                                                    {ChildIcon && (
                                                        <ChildIcon
                                                            size={16}
                                                            className="shrink-0"
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
                </div>

                <div className="flex flex-col gap-1">
                    {isAdmin && (
                        <Link
                            to="/settings/modules"
                            aria-label="Module Settings"
                            title={collapsed ? "Module Settings" : undefined}
                            className={cn(
                                "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors",
                                collapsed && "justify-center px-0",
                                isActive("/settings/modules")
                                    ? "bg-white text-priori-purple font-semibold"
                                    : "text-gray-600 hover:bg-white/50"
                            )}
                        >
                            <Settings size={18} />
                            {!collapsed && "Module Settings"}
                        </Link>
                    )}
                    <Link
                        to="help"
                        aria-label="Help"
                        title={collapsed ? "Help" : undefined}
                        className={cn(
                            "flex items-center gap-3 px-3 py-2.5 rounded-xl text-[13px] font-medium transition-colors",
                            collapsed && "justify-center px-0",
                            isActive("help")
                                ? "bg-sd-brand-bg text-sd-brand"
                                : "text-sd-muted hover:bg-sd-surface"
                        )}
                    >
                        <HelpCircle size={18} />
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
                            "flex items-center gap-3 px-3 py-2.5 rounded-xl text-[13px] font-medium text-sd-muted transition-colors hover:bg-sd-surface cursor-pointer",
                            collapsed && "justify-center px-0"
                        )}
                    >
                        <LogOut size={18} />
                        {!collapsed && "Logout"}
                    </button>
                    {!collapsed && (
                        <div className="mt-3 flex flex-col gap-3 border-t border-sd-border pt-4">
                            {user && (
                                <div className="flex items-center gap-2.5 px-1">
                                    <Avatar
                                        name={`${user.first_name} ${user.last_name}`}
                                        size={28}
                                        color="var(--color-sd-brand)"
                                    />
                                    <div className="min-w-0 leading-tight">
                                        <p className="truncate text-xs font-semibold text-sd-ink">
                                            {user.first_name} {user.last_name}
                                        </p>
                                        <p className="truncate text-[10px] text-sd-muted capitalize">
                                            {user.role}
                                        </p>
                                    </div>
                                </div>
                            )}
                            <p className="px-1 text-[10px] leading-relaxed text-sd-muted">
                                &copy; 2026 Business Central &middot; All Rights Reserved
                                <br />
                                Version: 1.0.188-288
                            </p>
                        </div>
                    )}
                </div>
            </div>
        </aside>
    );
}
