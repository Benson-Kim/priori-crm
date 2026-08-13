import { useEffect, useState } from "react";

import { getSalesReps, type RepPipelineCard } from "@/services/salesDeskApi";

/**
 * The reps a deal, prospect or company can be assigned to.
 *
 * The roster used to be a fixed reference list that callers could read
 * synchronously during render. It now comes from the server, so the owner
 * pickers load it here and default their selection once it arrives.
 */
export function useSalesReps(): {
    reps: RepPipelineCard[];
    isLoading: boolean;
    error: string | null;
} {
    const [reps, setReps] = useState<RepPipelineCard[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let active = true;

        getSalesReps()
            .then((loaded) => {
                if (active) setReps(loaded);
            })
            .catch((err) => {
                if (active)
                    setError(err instanceof Error ? err.message : "Could not load the team.");
            })
            .finally(() => {
                if (active) setIsLoading(false);
            });

        return () => {
            active = false;
        };
    }, []);

    return { reps, isLoading, error };
}
