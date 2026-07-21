import { useOwnerProfile } from "@/hooks/owner-profile-context";
import { resolvedReportingDate } from "@/lib/dateUtils";
import { useEffect, useState } from "react";

/**
 * Resolve the accounting day from the same organization configuration returned
 * by the API. The minute tick keeps long-lived tabs correct across midnight;
 * no timezone is hardcoded in the frontend.
 */
export function useReportingDate(): string {
  const { profile } = useOwnerProfile();
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const interval = window.setInterval(() => {
      setNow(Date.now());
    }, 60_000);
    return () => window.clearInterval(interval);
  }, []);

  return resolvedReportingDate(
    profile?.reportingTimezone,
    profile?.reportingDate,
    undefined,
    new Date(now)
  );
}
