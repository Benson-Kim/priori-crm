import { formatCurrency, formatDisplayDate } from "@/lib/utils";
import { Divider } from "../ui/Divider";
import { DocumentOwnerHeader } from "./DocumentOwnerHeader";
import { buildVatLabel } from "./utils";

export interface PurchaseOrderViewerData {
  poReference?: string;
  vendorId?: string;
  vendor?: {
    vendor_name: string;
    email?: string | null;
    phone_primary?: string | null;
    phone_secondary?: string | null;
    address?: string | null;
  };
  orderDate?: string;
  deliveryDate?: string | null;
  currency?: string;
  isRecurring?: boolean;
  complianceRef?: string | null;
  notes?: string;
  termsAndConditions?: string | null;
  subtotal: number;
  taxTotal: number;
  total: number;
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

interface PurchaseOrderViewerProps {
  data: PurchaseOrderViewerData;
  editableOwner?: boolean;
}

export function PurchaseOrderViewer({
  data,
  editableOwner = false,
}: Readonly<PurchaseOrderViewerProps>) {
  // Build VAT label from line items (shared helper used by the other viewers).
  const vatLabel = buildVatLabel(data.lineItems.map((i) => i.taxType));

  return (
    <div className="bg-white rounded-[20px] border-0 border-purple-25 overflow-hidden">
      {/* Top Section */}
        {/* Owner identity (logo + company block) - single source of truth. */}


      <div className="p-6 flex justify-between">
        <DocumentOwnerHeader editable={editableOwner} />
        <h2 className="text-[22px] font-black text-priori-purple tracking-wider mb-1 uppercase">
          PURCHASE ORDER
        </h2>
      </div>

      <div className="p-6">
        <div className="flex flex-col lg:flex-row lg:justify-between gap-12 items-start">
          {/* LEFT - Vendor */}
          <div className="flex flex-col gap-1 min-w-0">
            <p className="text-sm text-gray-500 mb-1 whitespace-nowrap">To</p>

            <p className="text-[16px] font-bold text-gray-800 truncate">
              {data.vendor?.vendor_name ?? data.vendorId}
            </p>

            {data.vendor && (
              <>
                {data.vendor.address && (
                  <p className="text-sm text-gray-600 whitespace-pre-wrap">
                    {data.vendor.address}
                  </p>
                )}

                <p className="text-sm text-gray-600">
                  {data.vendor.phone_primary ?? data.vendor.phone_secondary}
                </p>

                <p className="text-sm text-gray-600 break-all">
                  {data.vendor.email}
                </p>
              </>
            )}
          </div>

          {/* RIGHT - Metadata */}
          <div className="flex flex-col gap-6 min-w-0 self-end">
            <div className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-6 gap-y-3 items-start">
              <MetaField label="Reference">
                {data.poReference ?? "-"}
              </MetaField>

              <MetaField label="Order Date">
                {data.orderDate ? formatDisplayDate(data.orderDate) : "-"}
              </MetaField>

              <MetaField label="Delivery Date">
                {data.deliveryDate ? formatDisplayDate(data.deliveryDate) : "-"}
              </MetaField>


            </div>
          </div>
        </div>
      </div>

      <Divider />

      {/* Line Items */}
      <div className="p-6 flex flex-col gap-6">
        <h3 className="text-[20px] leading-7 font-bold text-gray-800">
          Item Details
        </h3>

        <div className="pb-4">
          <table className="w-full text-[16px] min-w-200">
            <thead>
              <tr className="bg-priori-purple text-white px-3 py-4 grid grid-cols-7 rounded-t-lg">
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

        {/* Purchase Order Totals (no Amount Paid / Balance Due - non-payable). */}
        <div className="w-full flex justify-end">
          <div className="min-w-80 flex flex-col gap-4 text-gray-800">
            <div className="flex justify-between items-center font-bold">
              <span>Subtotal</span>
              <span>
                {formatCurrency(data.subtotal, data.currency ?? "Ksh")}
              </span>
            </div>

            {data.taxTotal > 0 && (
              <>
                <Divider />
                <div className="flex justify-between items-center text-gray-800">
                  <span>{vatLabel ?? "VAT"}</span>
                  <span>
                    {formatCurrency(data.taxTotal, data.currency ?? "Ksh")}
                  </span>
                </div>
              </>
            )}

            <Divider />

            <div className="flex justify-between items-center font-bold text-[16px] text-gray-900">
              <span>Total</span>
              <span>{formatCurrency(data.total, data.currency ?? "Ksh")}</span>
            </div>
          </div>
        </div>
      </div>

      <div className="p-8 pt-6">
        <p className="text-[16px] font-bold text-gray-800 mb-3">Notes</p>
        <div className="w-full px-3 py-4 text-[16px] text-gray-700 rounded-xl min-h-15 whitespace-pre-wrap">
          {data.notes ?? "No notes added."}
        </div>

        {data.termsAndConditions && (
          <>
            <p className="text-[16px] font-bold text-gray-800 mb-3 mt-4">
              Terms &amp; Conditions
            </p>
            <div className="w-full px-3 py-4 text-[16px] text-gray-700 rounded-xl min-h-15 whitespace-pre-wrap">
              {data.termsAndConditions}
            </div>
          </>
        )}
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
