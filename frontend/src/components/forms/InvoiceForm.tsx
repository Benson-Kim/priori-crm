import { CURRENCY_OPTIONS, type CurrencyOption } from "@/lib/constants";
import {
  addDays,
  getDefaultDueDate,
  getTodayString,
  isDueDateBeforeTransactionDate,
} from "@/lib/dateUtils";
import { formatCurrency, formatDisplayDate } from "@/lib/utils";
import type {
  InvoiceCreatePayload,
  InvoiceResponse,
  LineItemPayload,
} from "@/services/invoiceApi";
import { Plus, Save, SquarePen, Trash } from "lucide-react";
import { Fragment, useMemo, useState } from "react";
import { DocumentOwnerHeader } from "../documents/DocumentOwnerHeader";
import { CustomerSelector } from "../modals/CustomerSelector";
import { Button } from "../ui/Button";
import { Divider } from "../ui/Divider";

interface InvoiceFormProps {
  initialData?: InvoiceResponse;
  onSave: (data: InvoiceCreatePayload) => Promise<void>;
  isLoading: boolean;
  onCancel: () => void;
  restrictedMode?: boolean;
}


interface LineItemRow {
  key: string;
  item_name: string;
  description: string;
  quantity: string;
  unitPrice: string;
  taxType: string;
}

function createEmptyRow(): LineItemRow {
  return {
    key: crypto.randomUUID(),
    item_name: "",
    description: "",
    quantity: "1",
    unitPrice: "0",
    taxType: "no_tax",
  };
}

function calcLineTotal(qty: string, price: string): number {
  const q = parseFloat(qty) || 0;
  const p = parseFloat(price) || 0;
  return q * p;
}

export function InvoiceForm({
  initialData,
  onSave,
  isLoading,
  restrictedMode = false,
}: Readonly<InvoiceFormProps>) {
  const [customerId, setCustomerId] = useState(initialData?.customer_id || "");

  const [transactionDate, setTransactionDate] = useState<string>(() => {
    const now = Date.now();
    return initialData?.transaction_date ?? getTodayString(now);
  });
  const [dueDate, setDueDate] = useState<string>(() => {
    const now = Date.now();
    return initialData?.due_date ?? getDefaultDueDate(30, now);
  });

  function handleTransactionDateChange(newDate: string) {
    setTransactionDate(newDate);

    // Auto-correct due date if it would become invalid
    if (isDueDateBeforeTransactionDate(dueDate, newDate)) {
      setDueDate(addDays(newDate, 30));
    }
  }

  const [currency, setCurrency] = useState(initialData?.currency || CURRENCY_OPTIONS[0].value);
  const [rfqNumber, setRfqNumber] = useState(initialData?.rfq_number || "");
  const [notes, setNotes] = useState(initialData?.notes || "");

  const [discountType, setDiscountType] = useState<
    "amount" | "percentage" | null
  >((initialData?.discount_type as "amount" | "percentage" | null) || null);
  const [discountValue, setDiscountValue] = useState<string>(
    initialData?.discount_type === "amount"
      ? String(initialData?.discount_amount || "")
      : initialData?.discount_type === "percentage"
        ? String(initialData?.discount_percentage || "")
        : ""
  );

  const [lineItems, setLineItems] = useState<LineItemRow[]>(() => {
    if (initialData?.line_items?.length) {
      return initialData.line_items.map((li) => ({
        key: li.id,
        item_name: li.item_name,
        description: li.description,
        quantity: String(li.quantity),
        unitPrice: String(li.unit_price),
        taxType: li.tax_type || "no_tax",
      }));
    }
    return [createEmptyRow()];
  });

  const [errors, setErrors] = useState<Record<string, string>>({});

  const totals = useMemo(() => {
    let subtotal = 0;
    let taxTotal = 0;
    for (const item of lineItems) {
      const lineTotal = calcLineTotal(item.quantity, item.unitPrice);
      subtotal += lineTotal;
      if (item.taxType === "vat_16") taxTotal += lineTotal * 0.16;
      if (item.taxType === "vat_8") taxTotal += lineTotal * 0.08;
    }

    const discountValueNum = parseFloat(discountValue) || 0;
    let discountAmount = 0;
    if (discountType === "amount") {
      discountAmount = Math.min(discountValueNum, subtotal);
    } else if (discountType === "percentage") {
      discountAmount = subtotal * (discountValueNum / 100);
    }

    const totalDue = subtotal - discountAmount + taxTotal;
    return { subtotal, taxTotal, discountAmount, totalDue };
  }, [lineItems, discountType, discountValue]);

  const addRow = () => setLineItems((prev) => [...prev, createEmptyRow()]);
  const removeRow = (key: string) =>
    setLineItems((prev) => prev.filter((r) => r.key !== key));
  const updateRow = (key: string, field: keyof LineItemRow, value: string) => {
    setLineItems((prev) =>
      prev.map((r) => (r.key === key ? { ...r, [field]: value } : r))
    );
  };

  const handleSubmit = async () => {
    const newErrors: Record<string, string> = {};
    if (!customerId) newErrors.customer = "Customer is required";
    if (!transactionDate)
      newErrors.transactionDate = "Transaction date is required";
    if (!dueDate) newErrors.dueDate = "Due date is required";
    if (isDueDateBeforeTransactionDate(dueDate, transactionDate))
      newErrors.dueDate = "Due date must be on or after transaction date";

    const validItems = lineItems.filter((r) => r.item_name.trim());
    if (validItems.length === 0)
      newErrors.lineItems = "At least one line item is required";

    for (const item of validItems) {
      const qty = parseFloat(item.quantity);
      const price = parseFloat(item.unitPrice);
      if (isNaN(qty) || qty <= 0)
        newErrors[`item_${item.key}_qty`] = "Quantity must be > 0";
      if (isNaN(price) || price < 0)
        newErrors[`item_${item.key}_price`] = "Price must be >= 0";
    }

    setErrors(newErrors);
    if (Object.keys(newErrors).length > 0) return;

    const items: LineItemPayload[] = validItems.map((r) => ({
      item_name: r.item_name.trim(),
      description: r.description.trim(),
      quantity: parseFloat(r.quantity),
      unitPrice: parseFloat(r.unitPrice),
      taxType: r.taxType || "no_tax",
    }));

    const payload: InvoiceCreatePayload = {
      customerId,
      transactionDate,
      dueDate,
      currency: currency as InvoiceCreatePayload["currency"],
      lineItems: items,
      rfqNumber: rfqNumber || undefined,
      notes: notes || undefined,
      discountType: discountType || undefined,
      discountAmount:
        discountType === "amount"
          ? parseFloat(discountValue) || undefined
          : undefined,
      discountPercentage:
        discountType === "percentage"
          ? parseFloat(discountValue) || undefined
          : undefined,
    };

    await onSave(payload);
  };

  return (
    <div className="flex flex-col gap-6 font-sans">
      <div className="bg-white rounded-[20px] border-2 border-purple-25 overflow-hidden shadow-sm">
        {/* Top Section */}
        <div className="p-6">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div className="md:col-span-2">
              <DocumentOwnerHeader editable={!restrictedMode} />
            </div>

            {/* Invoice To */}
            <div className="flex flex-col gap-1">
              <h2 className="text-[22px] font-black text-gray-800 tracking-wider mb-1 uppercase">
                INVOICE
              </h2>
              <CustomerSelector
                label="To"
                initialCustomerId={initialData?.customer_id}
                initialCustomerName={initialData?.customer?.display_name}
                initialCustomerDetails={
                  initialData?.customer
                    ? {
                      phone: initialData.customer.phone,
                      email: initialData.customer.email,
                    }
                    : null
                }
                onChange={(id) => setCustomerId(id)}
                restrictedMode={restrictedMode}
                error={errors.customer}
              />
            </div>
          </div>
        </div>

        {/* Metadata Row */}
        <div className="p-6 gap-6">
          <div className="grid grid-cols-2 md:grid-cols-10 gap-6">
            <div className="flex flex-col gap-2 md:col-span-2">
              <label className="text-gray-500 font-medium w-full">
                Reference
              </label>
              <p className="font-bold text-gray-800">
                {initialData?.invoice_reference || "Autogenerated"}
              </p>
            </div>

            <div className="flex flex-col gap-2 md:col-span-2">
              <label className="text-gray-500 font-medium w-full">
                Transaction Date
              </label>
              <div className="flex items-center gap-2 relative w-fit">
                <span className="font-bold text-priori-purple whitespace-nowrap">
                  {formatDisplayDate(transactionDate)}
                </span>
                {!restrictedMode && (
                  <SquarePen
                    size={20}
                    className="text-priori-purple pointer-events-none shrink-0"
                  />
                )}
                {!restrictedMode && (
                  <input
                    type="date"
                    value={transactionDate}
                    onChange={(e) =>
                      handleTransactionDateChange(e.target.value)
                    }
                    className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                  />
                )}
              </div>
              {errors.transactionDate && (
                <p className="text-xs text-red-500">{errors.transactionDate}</p>
              )}
            </div>

            <div className="flex flex-col gap-2 md:col-span-2">
              <label className="text-gray-500 font-medium w-full">
                Due Date
              </label>
              <div className="flex items-center gap-2 relative w-fit">
                <span className="font-bold text-priori-purple whitespace-nowrap">
                  {formatDisplayDate(dueDate)}
                </span>
                {!restrictedMode && (
                  <SquarePen
                    size={20}
                    className="text-priori-purple pointer-events-none shrink-0"
                  />
                )}
                {!restrictedMode && (
                  <input
                    type="date"
                    value={dueDate}
                    min={transactionDate}
                    onChange={(e) => setDueDate(e.target.value)}
                    className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                  />
                )}
              </div>
              {errors.dueDate && (
                <p className="text-xs text-red-500">{errors.dueDate}</p>
              )}
            </div>

            <div className="flex flex-col gap-2 min-w-0 md:col-span-3">
              <label className="text-gray-500 font-medium w-full">
                RFQ/RFP Number
              </label>
              <label
                htmlFor="rfq-input"
                className="flex items-center gap-2 w-fit max-w-full cursor-text"
              >
                <div className="relative flex items-center min-w-0">
                  <span className="invisible font-bold truncate block">
                    {rfqNumber || "Enter"}
                  </span>
                  <input
                    id="rfq-input"
                    type="text"
                    value={rfqNumber}
                    onChange={(e) => setRfqNumber(e.target.value)}
                    placeholder="Enter"
                    disabled={restrictedMode}
                    className="font-bold text-priori-purple outline-none bg-transparent w-full h-full placeholder-priori-purple/70 absolute inset-0 truncate"
                  />
                </div>
                {!restrictedMode && (
                  <SquarePen
                    size={20}
                    className="text-priori-purple shrink-0 cursor-pointer"
                  />
                )}
              </label>
            </div>

            <div className="flex flex-col gap-2 md:col-span-1">
              <label className="text-gray-500 font-medium w-full">
                Currency
              </label>
              <div className="flex items-center gap-2 relative w-fit min-w-25">
                <span className="font-bold text-priori-purple whitespace-nowrap">
                  {currency}
                </span>
                {!restrictedMode && (
                  <SquarePen
                    size={20}
                    className="text-priori-purple pointer-events-none shrink-0"
                  />
                )}
                {!restrictedMode && (
                  <select
                    value={currency}
                    onChange={(e) => setCurrency(e.target.value as CurrencyOption)}
                    className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                  >

                    {CURRENCY_OPTIONS.map((c) => (
                      <option key={c.value} value={c.value}>
                        {c.label}
                      </option>
                    ))}
                  </select>
                )}
              </div>
            </div>
          </div>
        </div>

        <Divider />

        {/* Line Items */}
        <div className="p-6 flex flex-col gap-6">
          <h3 className="text-[20px] leading-7.5 font-bold text-gray-800">
            Item Details
          </h3>
          {errors.lineItems && (
            <p className="text-xs text-red-500 mb-3">{errors.lineItems}</p>
          )}

          <div className="flex flex-col gap-3">
            <table className="w-full text-[16px]">
              <thead>
                <tr className="bg-priori-purple text-white px-3 py-4 first:rounded-tl-lg last:rounded-tr-lg grid grid-cols-7">
                  <th className="text-left px-3 font-bold leading-8 col-span-2">
                    Item
                  </th>
                  <th className="text-left px-3 font-bold leading-8 col-span-2">
                    Description
                  </th>
                  <th className="text-left px-3 font-bold leading-8">
                    Quantity
                  </th>
                  <th className="text-left px-3 font-bold leading-8">Price</th>
                  <th className="text-left px-3 font-bold leading-8">Total</th>
                </tr>
              </thead>
              <tbody>
                {lineItems.map((row) => {
                  const lineTotal = calcLineTotal(row.quantity, row.unitPrice);

                  const hasTax =
                    row.taxType !== "no_tax" && row.taxType !== undefined;
                  const taxCategory = row.taxType?.startsWith("vat")
                    ? "vat"
                    : row.taxType || "no_tax";
                  const taxRate =
                    row.taxType === "vat_16"
                      ? "16"
                      : row.taxType === "vat_8"
                        ? "8"
                        : "0";
                  const taxAmount =
                    row.taxType === "vat_16"
                      ? lineTotal * 0.16
                      : row.taxType === "vat_8"
                        ? lineTotal * 0.08
                        : 0;

                  const handleCategoryChange = (val: string) => {
                    if (val === "no_tax")
                      updateRow(row.key, "taxType", "no_tax");
                    else if (val === "exempt")
                      updateRow(row.key, "taxType", "exempt");
                    else updateRow(row.key, "taxType", "vat_16");
                  };

                  const handleRateChange = (val: string) => {
                    updateRow(row.key, "taxType", `vat_${val}`);
                  };

                  return (
                    <Fragment key={row.key}>
                      <tr className="group relative mb-3 grid grid-cols-7 gap-4 pt-2">
                        <td className="align-left col-span-2">
                          <input
                            type="text"
                            value={row.item_name || ""}
                            onChange={(e) =>
                              updateRow(row.key, "item_name", e.target.value)
                            }
                            placeholder="Item name"
                            disabled={restrictedMode}
                            className="w-full bg-transparent px-3 py-4 border border-gray-300 font-bold text-gray-800 placeholder-gray-300 rounded-lg"
                          />
                        </td>
                        <td className="align-top col-span-2">
                          <textarea
                            value={row.description}
                            onChange={(e) =>
                              updateRow(row.key, "description", e.target.value)
                            }
                            placeholder="Detailed description"
                            disabled={restrictedMode}
                            rows={1}
                            className="w-full bg-transparent px-3 py-4 border border-gray-300 text-gray-800 resize-none rounded-lg"
                          />
                        </td>
                        <td className="align-top">
                          <input
                            type="number"
                            value={row.quantity}
                            onChange={(e) =>
                              updateRow(row.key, "quantity", e.target.value)
                            }
                            disabled={restrictedMode}
                            className="w-full bg-transparent px-3 py-4 border border-gray-300 text-left text-gray-800 rounded-lg"
                          />
                        </td>
                        <td className="align-top">
                          <input
                            type="number"
                            value={row.unitPrice}
                            onChange={(e) =>
                              updateRow(row.key, "unitPrice", e.target.value)
                            }
                            disabled={restrictedMode}
                            className="w-full bg-transparent px-3 py-4 border border-gray-300 text-left text-gray-800 rounded-lg"
                          />
                        </td>
                        <td className="px-3 py-4 text-left border border-gray-100 bg-[#F8F9FA] text-gray-800 flex items-center font-bold rounded-lg">
                          {formatCurrency(lineTotal, "")}
                        </td>

                        {/* Delete button */}
                        {!restrictedMode && (
                          <div className="absolute right-0 top-1/2 -translate-y-1/2 translate-x-full pl-2 opacity-0 group-hover:opacity-100 transition-opacity">
                            <button
                              type="button"
                              onClick={() => removeRow(row.key)}
                              className="text-gray-400 hover:text-red-500 transition-colors"
                            >
                              <Trash size={20} />
                            </button>
                          </div>
                        )}
                      </tr>

                      {/* Tax Row */}
                      {hasTax ? (
                        <tr className="group relative mb-3 grid grid-cols-7 gap-4 items-center">
                          <td className="col-span-4 flex justify-end items-center pr-4 font-bold text-gray-800">
                            Tax
                          </td>
                          <td className="col-span-1">
                            <select
                              value={taxCategory}
                              onChange={(e) =>
                                handleCategoryChange(e.target.value)
                              }
                              disabled={restrictedMode}
                              className="w-full bg-white px-3 py-4 border border-gray-300 text-gray-800 rounded-lg outline-none font-medium cursor-pointer"
                            >
                              <option value="vat">VAT</option>
                              <option value="exempt">Exempt</option>
                            </select>
                          </td>
                          <td className="col-span-1">
                            {taxCategory === "vat" && (
                              <select
                                value={taxRate}
                                onChange={(e) =>
                                  handleRateChange(e.target.value)
                                }
                                disabled={restrictedMode}
                                className="w-full bg-white px-3 py-4 border border-gray-300 text-gray-800 rounded-lg outline-none font-medium cursor-pointer"
                              >
                                <option value="16">16%</option>
                                <option value="8">8%</option>
                                <option value="0">0%</option>
                              </select>
                            )}
                          </td>
                          <td className="px-3 py-4 text-left border border-gray-100 bg-[#F8F9FA] text-gray-800 flex items-center font-bold rounded-lg">
                            {formatCurrency(taxAmount, "")}
                          </td>

                          {/* Delete button for Tax Row */}
                          {!restrictedMode && (
                            <div className="absolute right-0 top-1/2 -translate-y-1/2 translate-x-full pl-2 opacity-0 group-hover:opacity-100 transition-opacity">
                              <button
                                type="button"
                                onClick={() =>
                                  updateRow(row.key, "taxType", "no_tax")
                                }
                                className="text-gray-400 hover:text-red-500 transition-colors"
                              >
                                <Trash size={20} />
                              </button>
                            </div>
                          )}
                        </tr>
                      ) : !restrictedMode ? (
                        <tr className="mb-3 grid grid-cols-7 gap-4">
                          <td className="col-span-7 flex justify-end">
                            <button
                              type="button"
                              onClick={() =>
                                updateRow(row.key, "taxType", "vat_16")
                              }
                              className="text-priori-purple text-sm font-bold hover:underline"
                            >
                              + Add Tax
                            </button>
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>

            {!restrictedMode && (
              <Button variant="outline" onClick={addRow} className="w-max">
                <Plus size={20} /> Add an Item
              </Button>
            )}

            {/* Subtotals */}
            <div className="w-full flex justify-end">
              <div className="min-w-80 flex flex-col gap-4 text-gray-800">
                {/* Subtotal */}
                <div className="flex justify-between items-center font-bold">
                  <span>Subtotal</span>
                  <span>{formatCurrency(totals.subtotal, "")}</span>
                </div>

                {/* Discount Row */}
                {!restrictedMode && discountType === null ? (
                  <button
                    type="button"
                    onClick={() => setDiscountType("percentage")}
                    className="text-priori-purple font-bold text-left hover:underline flex items-center"
                  >
                    + Add a discount
                  </button>
                ) : discountType !== null ? (
                  <div className="flex justify-between items-center">
                    <div className="flex items-center gap-2">
                      <span className="font-bold">Discount</span>
                      <select
                        value={discountType}
                        onChange={(e) =>
                          setDiscountType(
                            e.target.value as "amount" | "percentage"
                          )
                        }
                        disabled={restrictedMode}
                        className="bg-transparent border border-gray-300 rounded-lg px-3 py-3 text-sm outline-none cursor-pointer"
                      >
                        <option value="percentage">%</option>
                        <option value="amount">$</option>
                      </select>
                      <input
                        type="number"
                        value={discountValue}
                        onChange={(e) => setDiscountValue(e.target.value)}
                        disabled={restrictedMode}
                        className="w-16 bg-transparent border border-gray-300 rounded-lg px-3 py-3 text-sm outline-none text-center"
                        placeholder="0"
                      />
                      {!restrictedMode && (
                        <button
                          type="button"
                          onClick={() => {
                            setDiscountType(null);
                            setDiscountValue("");
                          }}
                          className="text-gray-400 hover:text-red-500 ml-1"
                        >
                          <Trash size={16} />
                        </button>
                      )}
                    </div>
                    <span>-{formatCurrency(totals.discountAmount, "")}</span>
                  </div>
                ) : null}

                <Divider className="opacity-50" />

                {/* VAT Row */}
                <div className="flex justify-between items-center text-gray-800">
                  <span>VAT</span>
                  <span>{formatCurrency(totals.taxTotal, "")}</span>
                </div>

                <Divider className="opacity-50" />

                {/* Total Due */}
                <div className="flex justify-between items-center font-bold text-[16px] text-gray-900 mt-1">
                  <span>Total Due</span>
                  <span>{formatCurrency(totals.totalDue, "")}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Notes */}
        <div className="p-8 pt-6">
          <label className="block text-[16px] font-bold text-gray-800 mb-3">
            Notes
          </label>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Add notes here"
            rows={1}
            className="w-full p-4 border border-gray-300 rounded-xl text-[16px] outline-none focus:border-priori-purple resize-none placeholder-gray-400"
          />
        </div>
      </div>

      {/* Footer Actions */}
      <div className="flex justify-end items-center gap-4 mt-2">
        <button
          type="button"
          onClick={() => alert("Preview not implemented")}
          className="px-8 py-3 border border-priori-purple text-priori-purple font-medium text-[16px] rounded-lg hover:bg-purple-50 transition-colors bg-white"
        >
          Preview
        </button>
        <button
          onClick={handleSubmit}
          disabled={isLoading}
          className="flex items-center gap-2 px-8 py-3 bg-priori-purple text-white font-medium text-[16px] rounded-lg hover:bg-priori-purple/90 transition-colors disabled:opacity-70"
        >
          <Save size={18} /> Save &amp; Continue
        </button>
      </div>
    </div>
  );
}
