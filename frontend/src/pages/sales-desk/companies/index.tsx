/**
 * Companies directory.
 *
 * Who the company is, who to call, which tenant they run on and how much
 * business is open with them. The billing side lives in the workspace.
 */

import { ChevronRight, Plus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/Button";
import { LoadingState } from "@/components/ui/LoadingState";
import { SearchInput } from "@/components/ui/SearchInput";
import { Table, type Column } from "@/components/ui/Table";
import { useDebounce } from "@/hooks/useDebounce";
import { getCompanyList, type CompanyRow } from "@/services/salesDeskApi";
import { RepAvatar } from "../components/RepAvatar";
import { CurrencyChip } from "./components/CurrencyChip";
import { NewCompanyDialog } from "./components/NewCompanyDialog";

const CELL_CLASS = "px-5 py-3 text-[13px]";
const TABLE_OVERRIDES =
    "[&_th]:bg-desk-bg [&_th]:text-[13px] [&_th]:text-desk-ink [&_table]:min-w-[1100px]";

/** First name only, since the column is narrow and the avatar disambiguates. */
const firstNameOf = (name: string) => name.split(" ")[0];

export default function SalesDeskCompaniesPage() {
    const navigate = useNavigate();

    const [companies, setCompanies] = useState<CompanyRow[]>([]);
    const [search, setSearch] = useState("");
    const debouncedSearch = useDebounce(search, 250);
    const [isCreating, setIsCreating] = useState(false);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Bumped after a company is registered so the list re-reads the store.
    const [revision, setRevision] = useState(0);

    useEffect(() => {
        let active = true;

        getCompanyList({ search: debouncedSearch })
            .then((rows) => {
                if (active) {
                    setCompanies(rows);
                    setError(null);
                }
            })
            .catch((err) => {
                if (active)
                    setError(err instanceof Error ? err.message : "Failed to load companies");
            })
            .finally(() => {
                if (active) setIsLoading(false);
            });

        return () => {
            active = false;
        };
    }, [debouncedSearch, revision]);

    const openInWorkspace = (company: CompanyRow) =>
        navigate(`/sales-desk/companies/workspace?company=${company.id}`);

    const columns = useMemo<Column<CompanyRow>[]>(
        () => [
            {
                key: "company",
                header: "Company",
                className: CELL_CLASS,
                render: (company) => (
                    <>
                        <span className="block font-medium text-desk-ink">{company.name}</span>
                        <span className="block text-xs text-desk-muted">{company.phone}</span>
                    </>
                ),
            },
            {
                key: "industry",
                header: "Industry",
                className: CELL_CLASS,
                render: (company) => (
                    <span className="text-desk-muted">{company.industry}</span>
                ),
            },
            {
                key: "contact",
                header: "Contact",
                className: CELL_CLASS,
                render: (company) => (
                    <>
                        <span className="block text-desk-ink">{company.contact}</span>
                        <span className="block text-xs text-desk-muted">{company.email}</span>
                    </>
                ),
            },
            {
                key: "tenant",
                header: "Tenant",
                className: CELL_CLASS,
                render: (company) => (
                    <span className="font-mono text-xs text-desk-muted">
                        {company.tenant ?? "—"}
                    </span>
                ),
            },
            {
                key: "currency",
                header: "Currency",
                className: CELL_CLASS,
                render: (company) => <CurrencyChip currency={company.primary_currency} />,
            },
            {
                key: "owner",
                header: "Owner",
                className: CELL_CLASS,
                render: (company) => (
                    <span className="flex items-center gap-2">
                        <RepAvatar
                            initials={company.owner_initials}
                            color={company.owner_color}
                            size="sm"
                        />
                        <span className="text-xs text-desk-muted">
                            {firstNameOf(company.owner_name)}
                        </span>
                    </span>
                ),
            },
            {
                key: "deals",
                header: "Deals",
                className: CELL_CLASS,
                render: (company) => (
                    <span className="flex items-center gap-1.5">
                        <span className="rounded-full bg-desk-accent-soft px-2 py-0.5 text-xs font-semibold text-priori-purple">
                            {company.total_deal_count} total
                        </span>
                        {company.open_deal_count > 0 && (
                            <span className="rounded-full bg-desk-blue-soft px-2 py-0.5 text-xs font-semibold text-desk-blue">
                                {company.open_deal_count} open
                            </span>
                        )}
                    </span>
                ),
            },
            {
                key: "open",
                header: "",
                className: `${CELL_CLASS} w-12 text-right`,
                render: (company) => (
                    <ChevronRight
                        className="ml-auto size-4 text-desk-muted"
                        aria-label={`Open ${company.name} billing profiles`}
                    />
                ),
            },
        ],
        []
    );

    return (
        <div className="flex flex-col gap-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
                <div>
                    <h1 className="text-2xl leading-8 font-bold text-desk-ink">Companies</h1>
                    <p className="pt-1 text-[13px] text-desk-muted">
                        {companies.length} {companies.length === 1 ? "company" : "companies"}
                    </p>
                </div>

                <div className="flex items-center gap-3">
                    <SearchInput
                        value={search}
                        onSearchChange={setSearch}
                        aria-label="Search companies"
                        placeholder="Search companies…"
                        className="w-56 bg-white px-3 py-2 [&_input]:text-[13px] [&_svg]:size-4"
                    />
                    <Button variant="primary" size="sm" onClick={() => setIsCreating(true)}>
                        <Plus size={16} /> New Company
                    </Button>
                </div>
            </div>

            {error && (
                <div role="alert" className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
                    {error}
                </div>
            )}

            {isLoading ? (
                <LoadingState message="Loading companies..." className="h-64" />
            ) : (
                <Table
                    columns={columns}
                    data={companies}
                    rowKey={(company) => String(company.id)}
                    onRowClick={openInWorkspace}
                    className={`overflow-hidden rounded-2xl border border-desk-border bg-desk-surface shadow-[0_1px_3px_rgba(0,0,0,0.06)] ${TABLE_OVERRIDES}`}
                    rowClassName={() => "border-desk-border hover:bg-desk-bg"}
                    emptyMessage="No companies match that search."
                />
            )}

            <NewCompanyDialog
                isOpen={isCreating}
                onClose={() => setIsCreating(false)}
                onCreated={() => {
                    setIsCreating(false);
                    setRevision((value) => value + 1);
                }}
            />
        </div>
    );
}
