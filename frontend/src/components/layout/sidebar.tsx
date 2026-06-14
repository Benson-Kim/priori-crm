import { useAuth } from "@/hooks/auth-context";
import { cn } from "@/lib/utils";
import { ChevronDown, HelpCircle, LogOut } from "lucide-react";
import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { navItems } from "./nav-items";

interface NavItem {
    path: string;
    label: string;
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

    const toggleGroup = (label: string) => {
        setManualOpen((prev) => ({
            ...prev,
            [label]: !prev[label],
        }));
    };

    return (
        <aside className="bg-gray-100 flex flex-col min-h-screen w-64 py-6 px-4">
            <div className="flex flex-col justify-between h-screen">
                <div className="flex flex-col gap-8">
                    <img src="/Logo Priori.svg" alt="Priori ogo" className="w-full px-2" />

                    <nav className="flex flex-col gap-2">

                        {navItems.map((item) => {
                            const Icon = item.icon;

                            if (!item.children) {
                                return (
                                    <Link
                                        key={item.path}
                                        to={item.path}
                                        className={cn(
                                            "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors",
                                            isActive(item.path)
                                                ? "bg-white text-priori-purple font-semibold"
                                                : "text-gray-600 hover:bg-white/50"
                                        )}
                                    >
                                        {Icon && <Icon size={18} />}
                                        {item.label}
                                    </Link>
                                );
                            }

                            const open =
                                manualOpen[item.label] ?? isGroupActive(item.children);

                            return (
                                <div key={item.label} className="flex flex-col">

                                    {/* GROUP HEADER */}
                                    <button
                                        onClick={() => toggleGroup(item.label)}
                                        className={cn(
                                            "flex items-center justify-between px-3 py-2.5 text-sm font-medium rounded-lg transition-colors",
                                            isGroupActive(item.children)
                                                ? "text-priori-purple font-semibold bg-white/50"
                                                : "text-gray-600 hover:bg-white/50"
                                        )}
                                    >
                                        <div className="flex items-center gap-3">
                                            {Icon && <Icon size={18} />}
                                            {item.label}
                                        </div>

                                        <ChevronDown
                                            size={16}
                                            className={cn(
                                                "transition-transform duration-200",
                                                open && "rotate-180"
                                            )}
                                        />
                                    </button>

                                    {/* CHILDREN */}
                                    <div
                                        className={cn(
                                            "flex flex-col gap-1 mt-1 pr-2 overflow-hidden transition-all duration-200",
                                            open
                                                ? "max-h-96 opacity-100"
                                                : "max-h-0 opacity-0 pointer-events-none"
                                        )}
                                    >
                                        {item.children.map((child) => (
                                            <Link
                                                key={child.path}
                                                to={child.path}
                                                className={cn(
                                                    "p-3 pl-6 flex items-center gap-3 rounded-xl text-sm font-medium transition-all border",
                                                    isActive(child.path)
                                                        ? "bg-white text-priori-purple font-bold border-gray-200"
                                                        : "text-gray-600 hover:bg-white/50 border-transparent hover:border-gray-200"
                                                )}
                                            >
                                                {child.label}
                                            </Link>
                                        ))}
                                    </div>
                                </div>
                            );
                        })}

                    </nav>
                </div>

                <div className="flex flex-col gap-1">
                    <Link
                        to="help"
                        className={cn(
                            "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors",
                            isActive("help")
                                ? "bg-white text-priori-purple font-semibold"
                                : "text-gray-600 hover:bg-white/50"
                        )}
                    >
                        <HelpCircle size={18} />
                        Help
                    </Link>


                    <button
                        type="button"
                        onClick={() => {
                            logout();
                            navigate("/login", { replace: true });
                        }}
                        className="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium text-gray-600 transition-colors hover:bg-white/50 cursor-pointer"
                    >
                        <LogOut size={18} />
                        Logout
                    </button>
                    <p className="pt-4 text-xs text-gray-600">
                        &copy; 2026 Priori — All Rights Reserved Version: 1.0.188-288
                    </p>
                </div>
            </div>
        </aside>
    );
}