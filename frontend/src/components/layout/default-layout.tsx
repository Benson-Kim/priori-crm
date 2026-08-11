import { useState } from "react";
import { Outlet, useMatches, type UIMatch } from "react-router-dom";

import type { RouteHandle } from "@/lib/types";
import { useAuth } from "@/hooks/auth-context";
import { OwnerProfileProvider } from "@/hooks/OwnerProfileProvider";
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
    const { user } = useAuth();

    const rawTitle = override?.title ?? header?.title;
    const activeTitle = rawTitle?.startsWith("Welcome,")
        ? user
            ? `Welcome, ${user.first_name}`
            : rawTitle
        : rawTitle;
    const activeDescription = override !== null ? override.description : header?.description;
    const showHeader = !!activeTitle;

    // Chrome badge counts (sidebar nav badges + topbar bell) are served by
    // issue #45's notifications endpoint and must never be computed
    // client-side. Until that endpoint lands, both stay undefined/zero and
    // the badges render hidden — replace with the notifications hook then.
    const navBadgeCounts = undefined;
    const notificationCount = 0;

    return (
        <OwnerProfileProvider>
            <HeaderContext.Provider value={{ override, setOverride }}>
                <div className="flex h-screen bg-gray-100 w-full overflow-hidden pt-2 pr-2">
                    <Sidebar badgeCounts={navBadgeCounts} />
                    <div className="flex flex-col flex-1 min-w-0 border border-gray-300 bg-gray-50 p-2 pb-4 rounded-2xl">
                        {showHeader && (
                            <Header
                                title={activeTitle}
                                description={activeDescription ?? ""}
                                notificationCount={notificationCount}
                            />
                        )}
                        <main className="flex-1 p-4 overflow-auto">
                            <Outlet />
                        </main>
                    </div>
                </div>
            </HeaderContext.Provider>
        </OwnerProfileProvider>
    );
};

export default DefaultLayout;