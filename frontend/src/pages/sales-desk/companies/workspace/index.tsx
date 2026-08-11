/**
 * Companies billing workspace.
 *
 * The accounting-facing view of the same book: each company's USD and KES
 * posting profiles side by side, and a one-click push for the ones that have
 * not reached accounting. The "Needs sync" tab is the working queue, and the
 * reason this screen exists separately from the directory.
 */

import { Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Button } from "@/components/ui/Button";
import { FilterTabs } from "@/components/ui/FilterTabs";
import { LoadingState } from "@/components/ui/LoadingState";
import { SearchInput } from "@/components/ui/SearchInput";
import { Table, type Column } from "@/components/ui/Table";
import { useDebounce } from "@/hooks/useDebounce";
import { cn } from "@/lib/utils";
import {
    getCompanyDetail,
    getCompanyList,
    syncBillingProfile,
    syncBothProfiles,
    updateBillingProfile,
    type BillingCurrency,
    type CompanyDetail,
    type CompanyRow,
} from "@/services/salesDeskApi";
import { RepAvatar } from "../../components/RepAvatar";
import { SyncStatus } from "../../components/SyncStatus";
import { CompanyDrawer } from "../components/CompanyDrawer";
import { NewCompanyDialog } from "../components/NewCompanyDialog";

const CELL_CLASS = "px-4 py-4 align-top";
const TABLE_OVERRIDES =
    "[&_th]:bg-desk-surface [&_th]:text-[13px] [&_th]:text-desk-ink [&_th]:py-3 [&_table]:min-w-[900px]";

type CompanyTabKey = "all" | "needs_sync";

export default function SalesDeskCompaniesWorkspacePage() {
    const [searchParams, setSearchParams] = useSearchParams();
    const selectedCompanyId = Number(searchParams.get("company")) || null;

    const [tab, setTab] = useState<CompanyTabKey>("all");
    const [search, setSearch] = useState("");
    const debouncedSearch = useDebounce(search, 250);
    const [isCreating, setIsCreating] = useState(false);

    const [allCompanies, setAllCompanies] = useState<CompanyRow[]>([]);
    const [detail, setDetail] = useState<CompanyDetail | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Bumped after every write so the page re-reads the mutated store.
    const [revision, setRevision] = useState(0);
    const refresh = useCallback(() => setRevision((value) => value + 1), []);

    // Stale-response guard: only the newest load may write state.
    const seqRef = useRef(0);
    useEffect(() => {
        const seq = ++seqRef.current;

        // Fetched unfiltered so the tab counts stay honest whichever tab shows.
        getCompanyList({ search: debouncedSearch })
            .then((rows) => {
                if (seq !== seqRef.current) return;
                setAllCompanies(rows);
                setError(null);
            })
            .catch((err) => {
                if (seq !== seqRef.current) return;
                setError(err instanceof Error ? err.message : "Failed to load companies");
            })
            .finally(() => {
                if (seq === seqRef.current) setIsLoading(false);
            });
    }, [debouncedSearch, revision]);

    // The drawer loads separately so it survives the table re-filtering.
    useEffect(() => {
        if (selectedCompanyId === null) {
            setDetail(null);
            return;
        }
        let active = true;
        getCompanyDetail(selectedCompanyId)
            .then((next) => {
                if (active) setDetail(next);
            })
            .catch(() => {
                if (active) setDetail(null);
            });
        return () => {
            active = false;
        };
    }, [selectedCompanyId, revision]);

    const selectCompany = useCallback(
        (companyId: number | null) => {
            setSearchParams(
                (params) => {
                    if (companyId === null) params.delete("company");
                    else params.set("company", String(companyId));
                    return params;
                },
                { replace: true }
            );
        },
        [setSearchParams]
    );

    const needsSyncCount = allCompanies.filter((company) => company.needs_sync).length;
    const rows = useMemo(
        () =>
            tab === "needs_sync"
                ? allCompanies.filter((company) => company.needs_sync)
                : allCompanies,
        [allCompanies, tab]
    );

    /** Push both profiles straight from the row, then re-read. */
    const syncRow = useCallback(
        async (companyId: number) => {
            await syncBothProfiles(companyId);
            refresh();
        },
        [refresh]
    );

    const columns = useMemo<Column<CompanyRow>[]>(
        () => [
            {
                key: "company",
                header: "Company",
                className: `${CELL_CLASS} w-[320px]`,
                render: (company) => (
                    <span className="flex items-start gap-2.5">
                        <RepAvatar
                            initials={company.owner_initials}
                            color={company.owner_color}
                        />
                        <span className="min-w-0">
                            <span
                                className={cn(
                                    "block text-[13px] font-semibold",
                                    company.id === selectedCompanyId
                                        ? "text-priori-purple"
                                        : "text-desk-ink"
                                )}
                            >
                                {company.name}
                            </span>
                            <span className="block text-[11px] text-desk-muted">
                                {company.industry} · {company.contact}
                            </span>
                        </span>
                    </span>
                ),
            },
            {
                key: "profiles",
                header: "Billing profiles",
                className: `${CELL_CLASS} w-[340px]`,
                render: (company) => (
                    <span className="flex flex-col gap-1">
                        {company.profiles.map((profile) => (
                            <span key={profile.code} className="flex items-center gap-3">
                                <span className="w-8 shrink-0 text-[11px] font-bold text-desk-ink">
                                    {profile.currency}
                                </span>
                                <span className="w-20 shrink-0 font-mono text-[11px] text-desk-muted">
                                    {profile.code}
                                </span>
                                <SyncStatus synced={profile.synced} />
                            </span>
                        ))}
                    </span>
                ),
            },
            {
                key: "deals",
                header: "Deals",
                className: `${CELL_CLASS} w-[120px]`,
                render: (company) => (
                    <span className="text-[13px] text-desk-ink">
                        <span className="block">{company.open_deal_count} open</span>
                        <span className="block text-[11px] text-desk-muted">
                            {company.closed_deal_count} closed
                        </span>
                    </span>
                ),
            },
            {
                key: "action",
                header: "",
                className: `${CELL_CLASS} text-right`,
                render: (company) =>
                    company.needs_sync ? (
                        <Button
                            variant="primary"
                            size="sm"
                            onClick={(event) => {
                                // The row opens the drawer; syncing is separate.
                                event.stopPropagation();
                                void syncRow(company.id);
                            }}
                        >
                            Sync both profiles
                        </Button>
                    ) : (
                        <span className="inline-block rounded-full bg-desk-green-soft px-3 py-1 text-xs font-semibold text-desk-green">
                            Synced
                        </span>
                    ),
            },
        ],
        [selectedCompanyId, syncRow]
    );

    return (
        // The drawer overlays the page, so nothing here reflows on selection.
        <>
            <div className="flex min-w-0 flex-col gap-4">
                <div className="flex items-center justify-between gap-4">
                    <h1 className="text-2xl leading-8 font-bold text-desk-ink">Companies</h1>
                    <Button variant="primary" size="sm" onClick={() => setIsCreating(true)}>
                        <Plus size={16} /> New Company
                    </Button>
                </div>

                {error && (
                    <div
                        role="alert"
                        className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700"
                    >
                        {error}
                    </div>
                )}

                <div className="shrink-0 overflow-hidden rounded-2xl border border-desk-border bg-desk-surface">
                    <div className="flex flex-wrap items-center gap-3 border-b border-desk-border px-4 py-3">
                        <FilterTabs
                            variant="segmented"
                            tabs={[
                                { key: "all", label: "All", count: allCompanies.length },
                                {
                                    key: "needs_sync",
                                    label: "Needs sync",
                                    count: needsSyncCount,
                                },
                            ]}
                            activeTab={tab}
                            onTabChange={(key) => setTab(key as CompanyTabKey)}
                            className="border-transparent bg-transparent shadow-none"
                        />

                        <SearchInput
                            value={search}
                            onSearchChange={setSearch}
                            aria-label="Search companies"
                            placeholder="Search companies…"
                            className="ml-auto w-56 bg-desk-bg px-3 py-1.5 [&_input]:text-xs [&_svg]:size-4"
                        />
                    </div>

                    {isLoading && allCompanies.length === 0 ? (
                        <LoadingState message="Loading companies..." className="h-64" />
                    ) : (
                        <Table
                            columns={columns}
                            data={rows}
                            rowKey={(company) => String(company.id)}
                            onRowClick={(company) => selectCompany(company.id)}
                            className={TABLE_OVERRIDES}
                            rowClassName={(company) =>
                                cn(
                                    "border-desk-border",
                                    company.id === selectedCompanyId
                                        ? "bg-desk-accent-soft"
                                        : "hover:bg-desk-bg"
                                )
                            }
                            emptyMessage={
                                tab === "needs_sync"
                                    ? "Every profile is in accounting."
                                    : "No companies match that search."
                            }
                        />
                    )}
                </div>
            </div>

            {detail && (
                <CompanyDrawer
                    // Remount per company so profile edits cannot leak across.
                    key={detail.company.id}
                    detail={detail}
                    onClose={() => selectCompany(null)}
                    onEditProfile={async (currency: BillingCurrency, patch) => {
                        await updateBillingProfile(detail.company.id, currency, patch);
                        refresh();
                    }}
                    onSyncProfile={async (currency: BillingCurrency) => {
                        await syncBillingProfile(detail.company.id, currency);
                        refresh();
                    }}
                    onSyncBoth={async () => {
                        await syncBothProfiles(detail.company.id);
                        refresh();
                    }}
                />
            )}

            <NewCompanyDialog
                isOpen={isCreating}
                onClose={() => setIsCreating(false)}
                onCreated={() => {
                    setIsCreating(false);
                    refresh();
                }}
            />
        </>
    );
}
