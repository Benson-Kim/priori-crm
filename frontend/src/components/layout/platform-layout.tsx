/**
 * Platform-operator console shell (issue #62 Phase B, ADR-0011).
 *
 * A third sibling shell next to `DefaultLayout` and `SalesDeskLayout`, with
 * deliberately MINIMAL chrome: no tenant sidebar, no tenant navigation, a
 * dark header band with an explicit "Platform operator" marker — the
 * operator context must be visually unmistakable from the tenant context,
 * because the two authority axes are disjoint (the operator administers
 * entitlements and holds no tenant-data access).
 *
 * Tenant navigation never links here; the console is reachable only for a
 * `platform_operator` user (RequirePlatformOperator wraps the route), and
 * RequireAuth routes operators here away from the tenant shells.
 */

import { LogOut, ShieldCheck } from "lucide-react";
import { Outlet } from "react-router-dom";

import { useAuth } from "@/hooks/auth-context";

export default function PlatformLayout() {
  const { user, logout } = useAuth();

  return (
    <div className="flex h-screen w-full flex-col overflow-hidden bg-gray-100">
      <header className="flex min-h-16 shrink-0 items-center justify-between gap-4 bg-slate-900 px-6 py-3 text-white">
        <div className="flex min-w-0 items-center gap-3">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-amber-400/20 text-amber-300">
            <ShieldCheck size={20} aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <h1 className="truncate text-[20px] leading-7.5 font-bold">
              Platform operator console
            </h1>
            <p className="truncate text-[12px] leading-4.5 text-slate-400">
              Module entitlements per owner organisation — not tenant data.
            </p>
          </div>
          <span className="ml-2 hidden shrink-0 rounded-full border border-amber-400/50 bg-amber-400/10 px-2.5 py-0.5 text-dense-sm font-semibold tracking-wide text-amber-300 uppercase sm:inline">
            Platform operator
          </span>
        </div>

        <div className="flex shrink-0 items-center gap-4">
          {user && (
            <span className="hidden truncate text-xs text-slate-300 md:inline">
              {user.email}
            </span>
          )}
          <button
            type="button"
            onClick={logout}
            className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-200 transition-colors hover:bg-slate-800"
          >
            <LogOut size={14} aria-hidden="true" />
            Sign out
          </button>
        </div>
      </header>

      <main className="relative flex-1 overflow-auto p-6">
        <Outlet />
      </main>
    </div>
  );
}
