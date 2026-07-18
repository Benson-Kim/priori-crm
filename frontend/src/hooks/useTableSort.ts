/**
 * useTableSort — client-side sort hook for the Table component.
 *
 * Smart comparator handles:
 *   - ISO date strings (YYYY-MM-DD)      → lexicographic (works correctly for ISO)
 *   - Decimal strings ("1234.56")        → numeric comparison
 *   - Numeric strings ("42")             → numeric comparison
 *   - All other strings                  → locale-aware string comparison
 *   - null / undefined                   → sorted to end in both directions
 *
 * Usage:
 *   const { sortedData, sortKey, sortDirection, handleSort } = useTableSort(data);
 *   // pass handleSort to Table's onSort prop
 */

import { useMemo, useState } from "react";

export type SortDirection = "asc" | "desc";

export interface UseTableSortReturn<T> {
  sortedData: T[];
  sortKey: string | null;
  sortDirection: SortDirection;
  handleSort: (key: string, direction: SortDirection) => void;
}

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const DECIMAL_RE = /^-?\d+(\.\d+)?$/;

function smartCompare(a: unknown, b: unknown): number {
  // Nulls / undefined → sort last
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;

  const sa = String(a);
  const sb = String(b);

  // ISO dates: lexicographic comparison is correct for YYYY-MM-DD
  if (ISO_DATE_RE.test(sa) && ISO_DATE_RE.test(sb)) {
    return sa < sb ? -1 : sa > sb ? 1 : 0;
  }

  // Decimal/numeric strings
  if (DECIMAL_RE.test(sa) && DECIMAL_RE.test(sb)) {
    return Number(sa) - Number(sb);
  }

  // Locale-aware string comparison
  return sa.localeCompare(sb, undefined, { sensitivity: "base" });
}

export function useTableSort<T extends Record<string, unknown>>(
  data: T[],
  initialKey: string | null = null,
  initialDirection: SortDirection = "desc"
): UseTableSortReturn<T> {
  const [sortKey, setSortKey] = useState<string | null>(initialKey);
  const [sortDirection, setSortDirection] = useState<SortDirection>(initialDirection);

  const handleSort = (key: string, direction: SortDirection) => {
    setSortKey(key);
    setSortDirection(direction);
  };

  const sortedData = useMemo(() => {
    if (!sortKey) return data;

    return [...data].sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      const cmp = smartCompare(av, bv);
      return sortDirection === "asc" ? cmp : -cmp;
    });
  }, [data, sortKey, sortDirection]);

  return { sortedData, sortKey, sortDirection, handleSort };
}
