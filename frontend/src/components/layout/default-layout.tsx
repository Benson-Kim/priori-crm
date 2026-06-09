import { useState } from "react";
import { Outlet, useMatches, type UIMatch } from "react-router-dom";

import type { RouteHandle } from "@/lib/types";
import { Header } from "./header";
import { HeaderContext, type HeaderOverride } from "./header-context";
import { Sidebar } from "./sidebar";

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