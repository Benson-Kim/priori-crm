export type components = {
  schemas: {
    UserRole: "admin" | "manager" | "member";
    CustomerStatus: "active" | "inactive" | "suspended" | "deleted";
    CustomerType: "individual" | "business";
    Currency: "KES" | "USD" | "EUR" | "GBP";
    TransactionType: "sale" | "refund" | "payment" | "adjustment";
    InvoiceStatus: "draft" | "sent" | "partial" | "paid" | "overdue" | "canceled";
    PaymentMethod: "cash" | "bank_transfer" | "check" | "card" | "mobile_money" | "other";
    TaxType: "vat_16" | "vat_8" | "vat_0" | "exempt" | "no_tax";
    DiscountType: "amount" | "percentage";
    QuoteStatus: "draft" | "sent" | "approved" | "invoiced" | "expired";
    VendorStatus: "active" | "inactive";
    PayableTransactionStatus: "paid" | "pending" | "overdue";
    ExpenseStatus: "pending" | "paid" | "overdue" | "canceled";
    DocumentSource: "form" | "view" | "payment_modal";
    PurchaseOrderStatus: "draft" | "sent" | "paid";
  };
};
