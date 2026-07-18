/**
 * useAgedReport — data-fetch hook for Aged Receivables and Aged Payables.
 *
 * Generic over response type T. Fetches on mount and when currency changes.
 * Exposes refetch for manual refresh.
 *
 * Usage:
 *   const { data, isLoading, error, currency, setCurrency } = useAgedReport({
 *     fetcher: getAgedReceivables,
 *   });
 */

import { useEffect, useState } from "react";
import { DEFAULT_CURRENCY } from "@/lib/constants";

interface UseAgedReportOptions<T> {
  fetcher: (currency: string) => Promise<T>;
  defaultCurrency?: string;
}

interface UseAgedReportReturn<T> {
  data: T | null;
  isLoading: boolean;
  error: string | null;
  currency: string;
  setCurrency: (currency: string) => void;
  refetch: () => void;
}

export function useAgedReport<T>({
  fetcher,
  defaultCurrency = DEFAULT_CURRENCY,
}: UseAgedReportOptions<T>): UseAgedReportReturn<T> {
  const [currency, setCurrency] = useState(defaultCurrency);
  const [data, setData] = useState<T | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // tick triggers manual refetch without changing currency
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);

    fetcher(currency)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const msg =
            err instanceof Error ? err.message : "Failed to load data";
          setError(msg);
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [currency, tick, fetcher]);

  return {
    data,
    isLoading,
    error,
    currency,
    setCurrency,
    refetch: () => setTick((t) => t + 1),
  };
}
