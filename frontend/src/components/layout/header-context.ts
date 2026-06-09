import { createContext, useContext, useEffect } from "react";

export type HeaderOverride = {
    title: string;
    description?: string;
} | null;

export const HeaderContext = createContext<{
    override: HeaderOverride;
    setOverride: (override: HeaderOverride) => void;
}>({
    override: null,
    setOverride: () => { },
});

export function useHeaderOverride(
    title: string | undefined,
    description: string | undefined = "",
) {
    const { setOverride } = useContext(HeaderContext);

    useEffect(() => {
        if (title !== undefined) {
            setOverride({ title, description });
        } else {
            setOverride(null);
        }
        return () => {
            setOverride(null);
        };
    }, [title, description, setOverride]);
}
