import { createContext, useContext, useEffect, useState } from "react";
import { Outlet, useMatches, type UIMatch } from "react-router-dom";

import type { RouteHandle } from "@/@types";
import { Header } from "./header";
import { Sidebar } from "./sidebar";

type HeaderOverride = {
    title: string;
    description?: string;
} | null;

export const HeaderContext = createContext<{
    override: HeaderOverride;
    setOverride: (override: HeaderOverride) => void;
}>({
    override: null,
    setOverride: () => {},
});

export function useHeaderOverride(title: string | undefined, description: string | undefined = "") {
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

const DefaultLayout = () => {
    const matches = useMatches() as UIMatch<unknown, RouteHandle>[];
    const [override, setOverride] = useState<HeaderOverride>(null);

    const current = [...matches]
        .reverse()
        .find((m) => m.handle?.header);

    const header = current?.handle?.header;

    const activeTitle = override?.title ?? header?.title;
    const activeDescription = override !== null ? override.description : header?.description;
    const showHeader = !!activeTitle;

    return (
        <HeaderContext.Provider value={{ override, setOverride }}>
            <div className="flex h-screen bg-gray-100 w-full overflow-hidden pt-2 pr-2">
                <Sidebar />
                <div className="flex flex-col flex-1 min-w-0 border border-gray-300 bg-gray-50 p-2 pb-4 rounded-2xl">
                    {showHeader && (
                        <Header
                            title={activeTitle}
                            description={activeDescription ?? ""}
                        />
                    )}
                    <main className="flex-1 p-4 overflow-auto">
                        <Outlet />
                    </main>
                </div>
            </div>
        </HeaderContext.Provider>
    );
};

export default DefaultLayout;