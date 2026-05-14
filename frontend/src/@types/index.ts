export type RouteHandle = {
  header?: {
    title: string;
    description: string;
  };
};

export interface PaginationMetadata {
  page: number;
  per_page: number;
  total: number;
  total_pages: number;
  has_next: boolean;
  has_prev: boolean;
}

export interface PaginatedApiResponse<T> {
  items: T[];
  metadata: PaginationMetadata;
}