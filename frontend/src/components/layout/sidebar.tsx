import { useAuth } from "@/hooks/auth-context";
import { cn } from "@/lib/utils";
import {
    ChevronDown,
    HelpCircle,
    LogOut,
    PanelLeftClose,
    PanelLeftOpen,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { navItems } from "./nav-items";

import type { ComponentType, SVGProps } from "react";

type SidebarIcon = ComponentType<
    SVGProps<SVGSVGElement> & {
        size?: number | string;
    }
>;

interface NavChild {
    path: string;
    label: string;
    icon?: SidebarIcon;
}

interface NavItem {
    path?: string;
    label: string;
    icon?: SidebarIcon;
    children?: NavChild[];
}

/** localStorage key persisting the collapsed/expanded preference. */
const COLLAPSED_STORAGE_KEY = "sidebar:collapsed";

function readInitialCollapsed(): boolean {
    if (typeof window === "undefined") return false;
    try {
        return window.localStorage.getItem(COLLAPSED_STORAGE_KEY) === "true";
    } catch {
        // localStorage may be unavailable (private mode / SSR) — default expanded.
        return false;
    }
}

export function Sidebar() {
    const { pathname } = useLocation();
    const navigate = useNavigate();
    const { logout } = useAuth();

    const isActive = (path: string) =>
        pathname === path || pathname.startsWith(path + "/");

    const isGroupActive = (children?: NavItem[]) =>
        children?.some((item) => isActive(item.path));

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
                "bg-gray-100 flex flex-col min-h-screen py-6 transition-all duration-200",
                collapsed ? "w-20 px-2" : "w-64 px-4"
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
                            <img
                                src="/Logo Priori.svg"
                                alt="Priori logo"
                                className="px-2 min-w-0"
                            />
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
                        {navItems.map((item) => {
                            const Icon = item.icon;

                            if (!item.children) {
                                return (
                                    <Link
                                        key={item.path}
                                        to={item.path}
                                        aria-label={item.label}
                                        title={collapsed ? item.label : undefined}
                                        className={cn(
                                            "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors",
                                            collapsed && "justify-center px-0",
                                            isActive(item.path)
                                                ? "bg-white text-priori-purple font-semibold"
                                                : "text-gray-600 hover:bg-white/50"
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
                                            "flex items-center px-3 py-2.5 text-sm font-medium rounded-lg transition-colors cursor-pointer",
                                            collapsed
                                                ? "justify-center px-0"
                                                : "justify-between",
                                            isGroupActive(item.children)
                                                ? "text-priori-purple font-semibold bg-white/50"
                                                : "text-gray-600 hover:bg-white/50"
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
                                                        "flex items-center gap-3 rounded-xl text-sm font-medium transition-all border",
                                                        collapsed
                                                            ? "justify-center px-1 py-2"
                                                            : "p-3 pl-6",
                                                        isActive(child.path)
                                                            ? "bg-white text-priori-purple font-bold border-gray-200"
                                                            : "text-gray-600 hover:bg-white/50 border-transparent hover:border-gray-200"
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
                    <Link
                        to="help"
                        aria-label="Help"
                        title={collapsed ? "Help" : undefined}
                        className={cn(
                            "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors",
                            collapsed && "justify-center px-0",
                            isActive("help")
                                ? "bg-white text-priori-purple font-semibold"
                                : "text-gray-600 hover:bg-white/50"
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
                            "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium text-gray-600 transition-colors hover:bg-white/50 cursor-pointer",
                            collapsed && "justify-center px-0"
                        )}
                    >
                        <LogOut size={18} />
                        {!collapsed && "Logout"}
                    </button>
                    {!collapsed && (
                        <p className="pt-4 text-xs text-gray-600">
                            &copy; 2026 Priori — All Rights Reserved Version: 1.0.188-288
                        </p>
                    )}
                </div>
            </div>
        </aside>
    );
}
