/**
 * Companies directory.
 *
 * Who the company is, who to call, which tenant they run on and how much
 * business is open with them. The billing side lives in the workspace.
 */

import { Plus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Avatar } from "@/components/ui/Avatar";
import { Button } from "@/components/ui/Button";
import { CurrencyChip } from "@/components/ui/Chip";
import { LoadingState } from "@/components/ui/LoadingState";
import { SearchInput } from "@/components/ui/SearchInput";
import { Table, type Column } from "@/components/ui/Table";
import { useDebounce } from "@/hooks/useDebounce";
import { getCompanyList, type CompanyRow } from "@/services/salesDeskApi";
import { NewCompanyDialog } from "./components/NewCompanyDialog";

const CELL_CLASS = "";

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
            .then((list) => {
                if (active) {
                    setCompanies(list.items);
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

    /*
     * A row leads to the workspace, where the profiles are actually worked.
     * The company is not carried across: opening a drawer the user has not
     * asked for hides the list they arrived to read, so the workspace opens
     * on its own list and they pick a row there.
     */
    const openInWorkspace = () => navigate("/sales-desk/companies/workspace");

    const columns = useMemo<Column<CompanyRow>[]>(
        () => [
            {
                key: "company",
                header: "Company",
                className: CELL_CLASS,
                render: (company) => (
                    // Clipped: a long company name would set the column width
                    // for every row and push the table into a scroll.
                    <>
                        <span
                            className="block max-w-[180px] truncate font-medium text-sd-ink"
                            title={company.name}
                        >
                            {company.name}
                        </span>
                        <span className="block text-xs text-sd-muted">{company.phone}</span>
                    </>
                ),
            },
            {
                key: "industry",
                header: "Industry",
                className: CELL_CLASS,
                render: (company) => (
                    <span className="text-sd-muted">{company.industry}</span>
                ),
            },
            {
                key: "contact",
                header: "Contact",
                className: CELL_CLASS,
                render: (company) => (
                    <>
                        <span
                            className="block max-w-[180px] truncate text-sd-ink"
                            title={company.contact ?? undefined}
                        >
                            {company.contact}
                        </span>
                        <span
                            className="block max-w-[180px] truncate text-xs text-sd-muted"
                            title={company.email ?? undefined}
                        >
                            {company.email}
                        </span>
                    </>
                ),
            },
            {
                key: "tenant",
                header: "Tenant",
                className: CELL_CLASS,
                render: (company) => (
                    <span className="font-mono text-xs text-sd-muted">
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
                        <Avatar
                            name={company.owner_name}
                            initials={company.owner_initials}
                            color={company.owner_color}
                            size={24}
                        />
                        <span className="text-xs text-sd-muted">
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
                        <span className="rounded-full bg-sd-brand-bg px-2 py-0.5 text-xs font-semibold text-sd-brand">
                            {company.total_deal_count} total
                        </span>
                        {company.open_deal_count > 0 && (
                            <span className="rounded-full bg-sd-info-bg px-2 py-0.5 text-xs font-semibold text-sd-info">
                                {company.open_deal_count} open
                            </span>
                        )}
                    </span>
                ),
            },
        ],
        []
    );

    return (
        <div className="flex flex-col gap-5">
            {/* No filters here, so search and the primary action hold the row. */}
            <div className="flex justify-end">
                <div className="flex w-full items-center gap-3 sm:w-auto">
                    <SearchInput
                        value={search}
                        onSearchChange={setSearch}
                        aria-label="Search companies"
                        placeholder="Search companies…"
                        className="h-control w-56 bg-sd-card px-3 [&_svg]:size-4"
                    />
                    {/*
                     * `h-control` on both, so the button and the search box
                     * are the same height by construction rather than by
                     * padding that happens to add up.
                     */}
                    <Button
                        variant="primary"
                        className="h-control"
                        onClick={() => setIsCreating(true)}
                    >
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
                <>
                    <Table
                        columns={columns}
                        data={companies}
                        rowKey={(company) => String(company.id)}
                        onRowClick={openInWorkspace}
                        variant="sales-desk"
                        className="shadow-sd-card [&_table]:min-w-[1100px]"
                        chevron={() => "Open the companies workspace"}
                        emptyMessage="No companies match that search."
                    />
                </>
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
