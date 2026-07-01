import { Badge } from "@/components/ui/Badge";
import { Dropdown, type DropdownItem } from "@/components/ui/Dropdown";
import { Input } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { Table } from "@/components/ui/Table";
import { formatCurrency, formatDisplayDate, saveBlob } from "@/lib/utils";
import {
    downloadVendorInvoicesPdf,
    downloadVendorPaymentsPdf,
    downloadVendorPurchaseOrdersPdf,
    exportVendorInvoicesExcel,
    exportVendorPaymentsExcel,
    exportVendorPurchaseOrdersExcel,
    getVendorInvoicesCard,
    getVendorPaymentsCard,
    getVendorPurchaseOrdersCard,
    type VendorCardDateRange,
    type VendorInvoicesCard,
    type VendorPaymentsCard,
    type VendorPurchaseOrdersCard,
} from "@/services/vendorApi";
import { Download } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import ExcelSvg from "@/assets/icons/excel-document-svgrepo-com.svg?react";

interface SupplierStatementsProps {
    vendorId: string;
    currency: string;
}

/** A card date-range filter: two independent date inputs. */
function DateRangeFilter({
    range,
    onChange,
}: {
    range: VendorCardDateRange;
    onChange: (next: VendorCardDateRange) => void;
}) {
    return (
        <div className="flex items-center gap-2">
            <Input
                type="date"
                aria-label="From date"
                value={range.dateFrom ?? ""}
                onChange={(e) => onChange({ ...range, dateFrom: e.target.value || undefined })}
                className="h-9 text-sm"
            />
            <span className="text-gray-400 text-sm">to</span>
            <Input
                type="date"
                aria-label="To date"
                value={range.dateTo ?? ""}
                min={range.dateFrom}
                onChange={(e) => onChange({ ...range, dateTo: e.target.value || undefined })}
                className="h-9 text-sm"
            />
        </div>
    );
}

/** Shared card shell: title, right-aligned date filter + actions dropdown. */
function StatementCard({
    title,
    range,
    onRangeChange,
    onExportExcel,
    onDownloadPdf,
    summary,
    children,
}: {
    title: string;
    range: VendorCardDateRange;
    onRangeChange: (next: VendorCardDateRange) => void;
    onExportExcel: () => void;
    onDownloadPdf: () => void;
    summary?: React.ReactNode;
    children: React.ReactNode;
}) {
    const actions: DropdownItem[] = [
        {
            key: "pdf",
            label: "Download PDF",
            icon: <Download size={16} />,
            onClick: onDownloadPdf,
        },
        {
            key: "excel",
            label: "Export Excel",
            icon: <ExcelSvg className="w-4 h-4" />,
            onClick: onExportExcel,
        },
    ];

    return (
        <div className="bg-white rounded-2xl border border-gray-200 p-4 flex flex-col gap-4">
            <div className="flex flex-col xl:flex-row justify-between items-start xl:items-center gap-3">
                <h4 className="text-[16px] font-bold text-gray-800">{title}</h4>
                <div className="flex items-center gap-3">
                    <DateRangeFilter range={range} onChange={onRangeChange} />
                    <Dropdown
                        items={actions}
                        className="flex items-center gap-2 px-3 py-2 border border-priori-purple text-priori-purple rounded-lg text-sm cursor-pointer hover:bg-purple-50 transition-colors"
                    />
                </div>
            </div>
            {summary}
            <div className="overflow-x-auto">{children}</div>
        </div>
    );
}

export function SupplierStatements({ vendorId, currency }: Readonly<SupplierStatementsProps>) {
    // Independent date-range state per card (changing one does not affect the
    // others).
    const [poRange, setPoRange] = useState<VendorCardDateRange>({});
    const [payRange, setPayRange] = useState<VendorCardDateRange>({});
    const [invRange, setInvRange] = useState<VendorCardDateRange>({});

    const [poCard, setPoCard] = useState<VendorPurchaseOrdersCard | null>(null);
    const [payCard, setPayCard] = useState<VendorPaymentsCard | null>(null);
    const [invCard, setInvCard] = useState<VendorInvoicesCard | null>(null);

    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const fetchPo = useCallback(async () => {
        try {
            setPoCard(await getVendorPurchaseOrdersCard(vendorId, poRange));
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load purchase orders");
        }
    }, [vendorId, poRange]);

    const fetchPay = useCallback(async () => {
        try {
            setPayCard(await getVendorPaymentsCard(vendorId, payRange));
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load payments");
        }
    }, [vendorId, payRange]);

    const fetchInv = useCallback(async () => {
        try {
            setInvCard(await getVendorInvoicesCard(vendorId, invRange));
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load invoices");
        }
    }, [vendorId, invRange]);

    useEffect(() => {
        void fetchPo();
    }, [fetchPo]);
    useEffect(() => {
        void fetchPay();
    }, [fetchPay]);
    useEffect(() => {
        void (async () => {
            setLoading(true);
            await fetchInv();
            setLoading(false);
        })();
    }, [fetchInv]);

    const handleExport = async (fn: () => Promise<Blob>, name: string) => {
        try {
            const blob = await fn();
            saveBlob(blob, name);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to export");
        }
    };

    const poColumns = [
        {
            key: "ref",
            header: "PO Ref",
            render: (r: VendorPurchaseOrdersCard["rows"][number]) => (
                <span className="text-gray-500 font-medium">{r.po_reference}</span>
            ),
        },
        {
            key: "date",
            header: "Date",
            render: (r: VendorPurchaseOrdersCard["rows"][number]) => (
                <span className="text-content-primary">{formatDisplayDate(r.order_date)}</span>
            ),
        },
        {
            key: "amount",
            header: "Amount",
            render: (r: VendorPurchaseOrdersCard["rows"][number]) => (
                <span className="text-gray-500">{formatCurrency(Number(r.amount), r.currency)}</span>
            ),
        },
    ];

    const payColumns = [
        {
            key: "number",
            header: "#.",
            render: (_r: VendorPaymentsCard["rows"][number], i: number) => (
                <span className="text-content-primary">{i + 1}.</span>
            ),
            className: "w-[50px]",
        },
        {
            key: "date",
            header: "Date",
            render: (r: VendorPaymentsCard["rows"][number]) => (
                <span className="text-content-primary">{formatDisplayDate(r.payment_date)}</span>
            ),
        },
        {
            key: "invoice",
            header: "Invoice #",
            render: (r: VendorPaymentsCard["rows"][number]) => (
                <span className="text-gray-500">{r.invoice_number ?? "\u2014"}</span>
            ),
        },
        {
            key: "reference",
            header: "Payment Ref #",
            render: (r: VendorPaymentsCard["rows"][number]) => (
                <span className="text-gray-500">{r.reference ?? "-"}</span>
            ),
        },
        {
            key: "amount",
            header: "Amount",
            render: (r: VendorPaymentsCard["rows"][number]) => (
                <span className="text-gray-500">{formatCurrency(Number(r.amount), r.currency)}</span>
            ),
        },
        {
            key: "document",
            header: "Document",
            render: (r: VendorPaymentsCard["rows"][number]) =>
                r.has_document ? (
                    <span className="text-priori-purple">Attached</span>
                ) : (
                    <span className="text-gray-400">\u2014</span>
                ),
        },
    ];

    const invColumns = [
        {
            key: "ref",
            header: "Reference",
            render: (r: VendorInvoicesCard["rows"][number]) => (
                <span className="text-gray-500 font-medium">{r.ref_no}</span>
            ),
        },
        {
            key: "date",
            header: "Date",
            render: (r: VendorInvoicesCard["rows"][number]) => (
                <span className="text-content-primary">{formatDisplayDate(r.invoice_date)}</span>
            ),
        },
        {
            key: "amount",
            header: "Amount",
            render: (r: VendorInvoicesCard["rows"][number]) => (
                <span className="text-gray-500">{formatCurrency(Number(r.amount), r.currency)}</span>
            ),
        },
        {
            key: "balance",
            header: "Balance",
            render: (r: VendorInvoicesCard["rows"][number]) => (
                <span className="text-gray-500">{formatCurrency(Number(r.balance), r.currency)}</span>
            ),
        },
    ];

    return (
        <div className="flex flex-col gap-4">
            <h3 className="text-[18px] font-bold text-gray-800">Supplier Statements</h3>

            {error && (
                <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
                    {error}
                </div>
            )}

            {loading && !poCard && !payCard && !invCard ? (
                <LoadingState message="Loading supplier statements..." />
            ) : (
                <div className="grid grid-cols-1 gap-4">
                    {/* Card 1 — Total POs */}
                    <StatementCard
                        title="Total POs"
                        range={poRange}
                        onRangeChange={setPoRange}
                        onExportExcel={() =>
                            handleExport(
                                () => exportVendorPurchaseOrdersExcel(vendorId, poRange),
                                `Vendor_PurchaseOrders_${new Date().toISOString().split("T")[0]}.xlsx`
                            )
                        }
                        onDownloadPdf={() =>
                            handleExport(
                                () => downloadVendorPurchaseOrdersPdf(vendorId, poRange),
                                `Vendor_PurchaseOrders_${new Date().toISOString().split("T")[0]}.pdf`
                            )
                        }
                        summary={
                            poCard && (
                                <div className="flex flex-wrap items-center gap-3">
                                    <Badge variant="paid">Paid: {poCard.paid}</Badge>
                                    <Badge variant="draft">Pending: {poCard.pending}</Badge>
                                    <Badge variant="overpaid">Overpaid: {poCard.overpaid}</Badge>
                                </div>
                            )
                        }
                    >
                        <Table
                            columns={poColumns}
                            data={poCard?.rows ?? []}
                            rowKey={(r) => r.id}
                            emptyMessage="No purchase orders for this period."
                        />
                    </StatementCard>

                    {/* Card 2 — Total Payments */}
                    <StatementCard
                        title="Total Payments"
                        range={payRange}
                        onRangeChange={setPayRange}
                        onExportExcel={() =>
                            handleExport(
                                () => exportVendorPaymentsExcel(vendorId, payRange),
                                `Vendor_Payments_${new Date().toISOString().split("T")[0]}.xlsx`
                            )
                        }
                        onDownloadPdf={() =>
                            handleExport(
                                () => downloadVendorPaymentsPdf(vendorId, payRange),
                                `Vendor_Payments_${new Date().toISOString().split("T")[0]}.pdf`
                            )
                        }
                    >
                        <Table
                            columns={payColumns}
                            data={payCard?.rows ?? []}
                            rowKey={(r) => r.id}
                            emptyMessage="No payments for this period."
                        />
                    </StatementCard>

                    {/* Card 3 — Total Invoices */}
                    <StatementCard
                        title="Total Invoices"
                        range={invRange}
                        onRangeChange={setInvRange}
                        onExportExcel={() =>
                            handleExport(
                                () => exportVendorInvoicesExcel(vendorId, invRange),
                                `Vendor_Invoices_${new Date().toISOString().split("T")[0]}.xlsx`
                            )
                        }
                        onDownloadPdf={() =>
                            handleExport(
                                () => downloadVendorInvoicesPdf(vendorId, invRange),
                                `Vendor_Invoices_${new Date().toISOString().split("T")[0]}.pdf`
                            )
                        }
                    >
                        <Table
                            columns={invColumns}
                            data={invCard?.rows ?? []}
                            rowKey={(r) => r.id}
                            emptyMessage="No invoices for this period."
                        />
                    </StatementCard>
                </div>
            )}
        </div>
    );
}