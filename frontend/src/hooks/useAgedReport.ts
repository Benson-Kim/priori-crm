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
  // tick triggers manual refetch without changing currency
  const [tick, setTick] = useState(0);

  // Identity of the fetch the UI currently wants. isLoading/error derive
  // from comparing it to the request that last settled, instead of being
  // set synchronously inside the effect (react-hooks/set-state-in-effect).
  const requestKey = `${currency}|${tick}`;
  const [settled, setSettled] = useState<{ key: string; error: string | null } | null>(
    null
  );

  useEffect(() => {
    let cancelled = false;

    fetcher(currency)
      .then((res) => {
        if (cancelled) return;
        setData(res);
        setSettled({ key: requestKey, error: null });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setSettled({
          key: requestKey,
          error: err instanceof Error ? err.message : "Failed to load data",
        });
      });

    return () => {
      cancelled = true;
    };
  }, [currency, tick, fetcher, requestKey]);

  const isLoading = settled?.key !== requestKey;
  const error = settled?.key === requestKey ? settled.error : null;

  return {
    data,
    isLoading,
    error,
    currency,
    setCurrency,
    refetch: () => setTick((t) => t + 1),
  };
}
