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

  // Build VAT label from line items
  const vatLabel = buildVatLabel(data.lineItems.map((i) => i.taxType));

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
      <div className="p-6">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* Owner identity (logo + company block) — single source of truth. */}
          <div className="md:col-span-2">
            <DocumentOwnerHeader />
          </div>

          {/* Document To */}
          <div className="flex flex-col gap-1">
            <h2 className="text-[22px] font-black text-gray-800 tracking-wider mb-1 uppercase">
              {label}
            </h2>
            <p className="text-sm text-gray-500 mb-1">To</p>
            <p className="text-[16px] font-bold text-priori-purple">
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
        </div>
      </div>

      {/*  Metadata Row  */}
      <div className="p-6">
        <div className="grid grid-cols-2 md:grid-cols-10 gap-6">
          <MetaField label="Reference" colSpan="md:col-span-2">
            {data.documentReference ?? "—"}
          </MetaField>
          <MetaField label="Transaction Date" colSpan="md:col-span-2">
            {data.transactionDate
              ? formatDisplayDate(data.transactionDate)
              : "—"}
          </MetaField>
          <MetaField label="Due Date" colSpan="md:col-span-2">
            {data.dueDate ? formatDisplayDate(data.dueDate) : "—"}
          </MetaField>
          <MetaField label="RFQ/RFP Number" colSpan="md:col-span-3">
            {data.rfqNumber ?? "—"}
          </MetaField>
          <MetaField label="Currency" colSpan="md:col-span-1">
            {data.currency ?? "—"}
          </MetaField>
        </div>
      </div>

      <Divider />

      {/*  Line Items  */}
      <div className="p-6 flex flex-col gap-6">
        <h3 className="text-[20px] leading-7 font-bold text-gray-800">
          Item Details
        </h3>

        <div className="pb-4">
          <table className="w-full text-[16px] min-w-[800px]">
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
        <div className="w-full px-3 py-4 text-[16px] text-gray-700 rounded-xl min-h-[60px] whitespace-pre-wrap">
          {data.notes ?? "No notes added."}
        </div>
      </div>
    </div>
  );
}

function MetaField({
  label,
  colSpan,
  children,
}: {
  label: string;
  colSpan?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={`flex flex-col gap-2 ${colSpan ?? ""}`}>
      <span className="text-gray-500 font-medium">{label}</span>
      <span className="font-bold text-gray-800 truncate">{children}</span>
    </div>
  );
}
