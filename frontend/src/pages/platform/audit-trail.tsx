/**
 * Platform audit trail (issue #71, ADR-0014 Phase A).
 *
 * Read-only view over GET /platform/audit: every entitlement
 * grant/revocation and tenant lifecycle change the operator (or the
 * system) performed, newest first, with action filter, optional
 * selected-owner scoping and page-number pagination.
 *
 * Behaviour rules (inherited from the console's consolidation review):
 * - The list is always the server's state — refreshed explicitly, never
 *   locally synthesised.
 * - Empty/error states render explanatory copy with a retry action; an
 *   unreachable API must never look like a blank panel.
 */

import { RefreshCw, ScrollText, ServerOff } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { LoadingState } from "@/components/ui/LoadingState";
import { Pagination } from "@/components/ui/Pagination";
import { moduleLabel } from "@/lib/module-labels";
import {
  getPlatformAudit,
  type PlatformAuditEvent,
  type PlatformAuditPage,
} from "@/services/platformApi";

const ACTION_FILTERS: { value: string; label: string }[] = [
  { value: "", label: "All actions" },
  { value: "module_enabled", label: "Module enabled" },
  { value: "module_disabled", label: "Module disabled" },
  { value: "owner_suspended", label: "Owner suspended" },
  { value: "owner_reactivated", label: "Owner reactivated" },
];

const ACTION_LABELS: Record<string, string> = {
  module_enabled: "Module enabled",
  module_disabled: "Module disabled",
  owner_suspended: "Owner suspended",
  owner_reactivated: "Owner reactivated",
};

const ACTION_TONES: Record<string, string> = {
  module_enabled: "bg-green-50 text-green-700 border-green-200",
  module_disabled: "bg-amber-50 text-amber-700 border-amber-200",
  owner_suspended: "bg-red-50 text-red-700 border-red-200",
  owner_reactivated: "bg-green-50 text-green-700 border-green-200",
};

function formatInstant(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** One human sentence for an event's before → after payload. */
function describeChange(event: PlatformAuditEvent): string {
  const before = event.before ?? {};
  const after = event.after ?? {};
  if (event.entityType === "owner_module_setting") {
    const key = String(after["module_key"] ?? before["module_key"] ?? "");
    const to = after["enabled"] ? "granted" : "revoked";
    return `${moduleLabel(key)} ${to} (was ${before["enabled"] ? "enabled" : "disabled"})`;
  }
  if (event.entityType === "owner_profile") {
    return `status ${String(before["status"] ?? "?")} → ${String(after["status"] ?? "?")}`;
  }
  return event.entityType;
}

export default function AuditTrail({
  ownerId,
  ownerName,
}: Readonly<{ ownerId?: string | null; ownerName?: string | null }>) {
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(10);
  const [action, setAction] = useState("");
  const [scopeToOwner, setScopeToOwner] = useState(false);

  const [data, setData] = useState<PlatformAuditPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // Bumped by the Refresh/retry buttons to force a refetch with unchanged
  // filters. All setState happens in promise callbacks (never synchronously
  // inside the effect body), so this loader adds no lint-warning debt.
  const [refreshKey, setRefreshKey] = useState(0);

  const effectiveOwnerId = scopeToOwner ? (ownerId ?? undefined) : undefined;

  const refresh = useCallback(() => setRefreshKey((key) => key + 1), []);

  useEffect(() => {
    let cancelled = false;
    getPlatformAudit({
      page,
      perPage,
      action: action || undefined,
      ownerId: effectiveOwnerId,
      withTotal: true,
    })
      .then((response) => {
        if (cancelled) return;
        setData(response);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setData(null);
        setError(
          err instanceof Error ? err.message : "Failed to load the audit trail"
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [page, perPage, action, effectiveOwnerId, refreshKey]);

  const items = data?.items ?? [];
  const totalPages = data?.metadata.total_pages ?? 1;

  return (
    <section className="flex flex-col gap-3">
      <div>
        <h2 className="text-lg font-semibold text-gray-900">Audit trail</h2>
        <p className="text-sm text-gray-500">
          Every entitlement and tenant-lifecycle change, newest first — the
          append-only evidence record behind the weekly operator review.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <select
          value={action}
          onChange={(e) => {
            setAction(e.target.value);
            setPage(1);
          }}
          aria-label="Filter by action"
          className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700"
        >
          {ACTION_FILTERS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        {ownerId && (
          <label className="flex cursor-pointer items-center gap-2 text-sm text-gray-600">
            <input
              type="checkbox"
              checked={scopeToOwner}
              onChange={(e) => {
                setScopeToOwner(e.target.checked);
                setPage(1);
              }}
              className="size-4 accent-priori-purple"
            />
            Only {ownerName ?? "the selected organisation"}
          </label>
        )}
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="ml-auto text-sm"
          onClick={refresh}
        >
          <RefreshCw size={14} aria-hidden="true" />
          Refresh
        </Button>
      </div>

      {loading && data === null && (
        <LoadingState message="Loading the audit trail..." />
      )}

      {error && (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-red-200 bg-red-50 px-6 py-10 text-center">
          <ServerOff size={28} className="text-red-400" aria-hidden="true" />
          <div>
            <p className="text-sm font-semibold text-red-700">
              The audit trail could not be loaded.
            </p>
            <p className="mt-1 text-sm text-red-600">
              Nothing is wrong with the underlying records. ({error})
            </p>
          </div>
          <Button
            type="button"
            variant="outline-danger"
            size="sm"
            className="text-sm"
            onClick={refresh}
          >
            <RefreshCw size={14} aria-hidden="true" />
            Try again
          </Button>
        </div>
      )}

      {data !== null && !error && items.length === 0 && (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-gray-200 bg-white px-6 py-10 text-center">
          <ScrollText size={28} className="text-gray-400" aria-hidden="true" />
          <p className="text-sm text-gray-500">
            No audit events match the current filters. Entitlement and
            lifecycle changes will appear here as they are made.
          </p>
        </div>
      )}

      {data !== null && !error && items.length > 0 && (
        <>
          <ul className="divide-y divide-gray-200 rounded-xl border border-gray-200 bg-white">
            {items.map((event) => (
              <li
                key={event.id}
                className="flex flex-col gap-1 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      className={`rounded-full border px-2.5 py-0.5 text-xs font-semibold ${
                        ACTION_TONES[event.action] ??
                        "border-gray-200 bg-gray-50 text-gray-600"
                      }`}
                    >
                      {ACTION_LABELS[event.action] ?? event.action}
                    </span>
                    <span className="truncate text-sm text-gray-700">
                      {describeChange(event)}
                    </span>
                  </div>
                  <p className="mt-0.5 text-xs text-gray-400">
                    {event.actorId
                      ? `Actor ${event.actorId}`
                      : "System (no actor)"}
                  </p>
                </div>
                <time
                  dateTime={event.createdAt}
                  className="shrink-0 text-xs text-gray-400"
                >
                  {formatInstant(event.createdAt)}
                </time>
              </li>
            ))}
          </ul>
          {totalPages > 1 && (
            <Pagination
              currentPage={page}
              totalPages={totalPages}
              perPage={perPage}
              onPageChange={setPage}
              onPerPageChange={(next) => {
                setPerPage(next);
                setPage(1);
              }}
            />
          )}
        </>
      )}
    </section>
  );
}
