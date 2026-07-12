/**
 * DocumentViewer — read-only formatted view for Invoice and Quote.
 * Used for detail pages and PDF preview.
 */

import { formatCurrency, formatDisplayDate } from "@/lib/utils";
import { Divider } from "../ui/Divider";
import { DocumentOwnerHeader } from "./DocumentOwnerHeader";
import { DocumentTotalsPanel } from "./layout/document-totals";
import { buildVatLabel, type DocumentTotals } from "./utils";

export interface DocumentViewerData {
  documentReference?: string;
  customerId?: string;
  customer?: {
    display_name: string;
    email: string;
    phone: string;
    address?: string;
  };
  transactionDate?: string;
  dueDate?: string;
  currency?: string;
  rfqNumber?: string;
  notes?: string;
  discountType?: "amount" | "percentage" | null;
  discountAmount?: number | null;
  discountPercentage?: number | null;
  subtotal: number;
  taxTotal: number;
  totalDue: number;
  amountPaid?: number;
  balanceDue?: number;
  vatEnabled?: boolean;
  vatRate?: number | null;
  vatComplianceRef?: string | null;
  lineItems: {
    id: string;
    itemName?: string;
    description: string;
    quantity: number;
    unitPrice: number;
    taxType?: string;
    lineTotal: number;
  }[];
}

interface DocumentViewerProps {
  type: "invoice" | "quote";
  data: DocumentViewerData;
}

export function DocumentViewer({ type, data }: Readonly<DocumentViewerProps>) {
  const label = type === "invoice" ? "INVOICE" : "QUOTE";

  // Build VAT label 
  const vatEnabled = Boolean(data.vatEnabled || data.taxTotal > 0);

  const vatLabel = vatEnabled
    ? buildVatLabel(data.vatRate, data.vatComplianceRef)
    : "VAT";

  // Calculate the actual discount amount
  const calculatedDiscount = data.subtotal - (data.totalDue - data.taxTotal);
  const actualDiscountAmount =
    data.discountAmount ?? (calculatedDiscount > 0 ? calculatedDiscount : 0);

  // Construct totals for DocumentTotalsPanel (read-only mode)
  const totals: DocumentTotals = {
    subtotal: data.subtotal,
    taxTotal: data.taxTotal,
    totalDue: data.totalDue,
    discountAmount: actualDiscountAmount,
  };

  return (
    <div className="bg-white rounded-[20px] border-2 border-purple-25 overflow-hidden">
      {/*  Top Section  */}
      {/* Owner identity (logo + company block) — single source of truth. */}
      <div className="p-6 flex justify-between">
        <DocumentOwnerHeader />
        <h2 className="text-[22px] text-priori-purple  tracking-wide font-bold mb-1 uppercase">
          {label}
        </h2>
      </div>

      <div className="p-6">
        <div className="flex flex-col lg:flex-row lg:justify-between gap-12 items-start">

          {/* Document To */}
          <div className="flex flex-col gap-1">

            <p className="text-sm text-gray-500 mb-1">To</p>
            <p className="text-[16px] font-bold text-gray-800 truncate">
              {data.customer?.display_name ?? data.customerId}
            </p>
            {data.customer && (
              <>
                {data.customer.address && (
                  <p className="text-sm text-gray-600">
                    {data.customer.address}
                  </p>
                )}
                <p className="text-sm text-gray-600">{data.customer.phone}</p>
                <p className="text-sm text-gray-600">{data.customer.email}</p>
              </>
            )}
          </div>

          {/*  Metadata Row  */}
          <div className="flex flex-col gap-6 min-w-0 self-end">
            <div className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-6 gap-y-3 items-start">
              <MetaField label="Reference">
                {data.documentReference ?? "—"}
              </MetaField>
              <MetaField label="Transaction Date">
                {data.transactionDate
                  ? formatDisplayDate(data.transactionDate)
                  : "—"}
              </MetaField>
              <MetaField label="Due Date">
                {data.dueDate ? formatDisplayDate(data.dueDate) : "—"}
              </MetaField>
              <MetaField label="RFQ/RFP Number">
                {data.rfqNumber ?? "—"}
              </MetaField>
              <MetaField label="Currency">
                {data.currency ?? "—"}
              </MetaField>
            </div>
          </div>
        </div>
      </div>
      <Divider />

      {/*  Line Items  */}
      <div className="p-6 flex flex-col gap-6">
        <h3 className="text-[20px] leading-7 font-bold text-gray-800">
          Item Details
        </h3>

        <div className="pb-4">
          <table className="w-full text-[16px] min-w-200">
            <thead>
              <tr className="bg-priori-purple text-white px-4 py-3 grid grid-cols-7 rounded-t-lg">
                <th className="text-left px-3 font-bold leading-8 col-span-4">
                  Item
                </th>
                <th className="text-center px-3 font-bold leading-8">
                  Quantity
                </th>
                <th className="text-right px-3 font-bold leading-8">Price</th>
                <th className="text-right px-3 font-bold leading-8">Total</th>
              </tr>
            </thead>
            <tbody>
              {data.lineItems.map((item) => (
                <tr
                  key={item.id}
                  className="grid grid-cols-7 gap-4 pt-2 border-b border-gray-100 last:border-0"
                >
                  <td className="col-span-4 px-3 py-4 flex flex-col gap-1">
                    <span className="font-bold text-gray-800">
                      {item.itemName ?? item.description.split("\n")[0] ?? ""}
                    </span>
                    <span className="text-gray-600 text-sm whitespace-pre-wrap">
                      {item.description}
                    </span>
                  </td>
                  <td className="px-3 py-4 text-center text-gray-800">
                    {item.quantity}
                  </td>
                  <td className="px-3 py-4 text-right text-gray-800">
                    {formatCurrency(item.unitPrice, "")}
                  </td>
                  <td className="px-3 py-4 text-right font-medium text-gray-800">
                    {formatCurrency(item.lineTotal, "")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Totals — read-only mode */}
        <DocumentTotalsPanel
          totals={totals}
          currency={data.currency ?? ""}
          discountType={data.discountType ?? null}
          discountValue={
            data.discountType === "percentage"
              ? String(data.discountPercentage ?? 0)
              : String(data.discountAmount ?? 0)
          }
          vatEnabled={vatEnabled}
          vatLabel={vatLabel}
          restrictedMode={true}
          onDiscountTypeChange={() => { }}
          onDiscountValueChange={() => { }}
          onDiscountRemove={() => { }}
          onAddDiscount={() => { }}
          amountPaid={data.amountPaid}
          balanceDue={data.balanceDue}
        />
      </div>

      <div className="p-8 pt-6">
        <p className="text-[16px] font-bold text-gray-800 mb-3">Notes</p>
        <div className="w-full px-3 py-4 text-[16px] text-gray-700 rounded-xl min-h-15 whitespace-pre-wrap">
          {data.notes ?? "No notes added."}
        </div>
      </div>
    </div>
  );
}

function MetaField({
  label,
  children,
  tooltip,
}: {
    label: string;
  children: React.ReactNode;
    tooltip?: string;
}) {
  return (
    <>
      <span
        title={tooltip}
        className={`text-[16px] font-bold text-gray-800 whitespace-nowrap${tooltip ? " cursor-help" : ""}`}
      >
        {label}
      </span>

      <span className="text-[16px] text-gray-800 min-w-0 truncate">
        {children}
      </span>
    </>
  );
}