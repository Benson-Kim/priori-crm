/**
 * Human labels for the per-owner module keys (ADR-0011).
 *
 * Shared by the tenant's read-only Settings › Modules screen and the
 * platform-operator console so the two surfaces can never drift apart on
 * naming. Unknown keys fall back to a prettified form of the key itself,
 * so a newly added backend module renders sensibly before a label lands.
 */

export const MODULE_LABELS: Record<string, string> = {
  customers: "Customers",
  quotes: "Quotes",
  invoices: "Invoices",
  deals: "Deals",
  nurture: "Nurture (Future Pipeline)",
  onboarding: "Onboarding",
  vendors: "Vendors",
  expenses: "Expenses",
  purchase_orders: "Purchase Orders",
  statements: "Statements",
  reports: "Reports",
  sales_desk: "Sales Desk",
  auth: "Authentication",
  owner: "Organisation Profile",
  health: "Health",
  dashboard: "Dashboard",
};

export function moduleLabel(key: string): string {
  return (
    MODULE_LABELS[key] ??
    key
      .split("_")
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ")
  );
}
