export type RouteHandle = {
  header?: {
    title: string;
    description: string;
  };
};

export interface PaginationMetadata {
  page: number;
  per_page: number;
  total?: number;
  total_pages?: number;
  has_next: boolean;
  has_prev: boolean;
}

export interface PaginatedApiResponse<T> {
  items: T[];
  metadata: PaginationMetadata;
}

export interface PaginatedResult<T> {
  items: T[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

export interface StatementTransaction {
  date: string;
  description: string;
  amount: number;
  payment: number;
  balance: number;
}

export interface StatementSummary {
  opening_balance: number;
  invoiced_amount: number;
  amount_paid: number;
  balance_due: number;
}

export interface CustomerStatement {
  customer: {
    id: string;
    customer_type: string;
    company_name: string | null;
    first_name: string;
    last_name: string;
    email: string;
    phone: string;
    website: string | null;
    vat_number: string | null;
    currency: string;
    address: string;
    address2: string | null;
    country: string;
    province: string;
    city: string;
    postal_code: string;
    status: string;
    balance: number;
    created_at: string | null;
    updated_at: string | null;
  };
  period_start: string;
  period_end: string;
  summary: StatementSummary;
  transactions: StatementTransaction[];
  generated_at: string;
}