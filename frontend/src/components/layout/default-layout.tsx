import { Outlet, useMatches, type UIMatch } from "react-router-dom";

import type { RouteHandle } from "@/@types";
import { Header } from "./header";
import { Sidebar } from "./sidebar";

const DefaultLayout = () => {
    const matches = useMatches() as UIMatch<unknown, RouteHandle>[];

    const current = [...matches]
        .reverse()
        .find((m) => m.handle?.header);

    const header = current?.handle?.header;

    return (
        <div className="flex h-screen bg-gray-100 w-full overflow-hidden pt-2 pr-2">
            <Sidebar />
            <div className="flex flex-col flex-1 min-w-0 border border-gray-300 bg-gray-50 p-2 pb-4 rounded-2xl">
                {header && (
                    <Header
                        title={header.title}
                        description={header.description}
                    />
                )}
                <main className="flex-1 p-4 overflow-auto">
                    <Outlet />
                </main>
            </div>
        </div>
    );
};

export default DefaultLayout;