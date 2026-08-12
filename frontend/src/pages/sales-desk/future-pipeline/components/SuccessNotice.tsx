/**
 * SuccessNotice
 *
 * Inline confirmation banner, the success twin of the inline error notice
 * every desk screen renders. Local to the future-pipeline pages; page
 * modules do not import across each other.
 *
 * Dismisses itself after a short pause so a confirmation never needs to be
 * cleared by hand, and never outstays the action it confirms.
 */

import { useEffect } from "react";

const DISMISS_AFTER_MS = 6_000;

interface SuccessNoticeProps {
    message: string;
    onDismiss: () => void;
}

export function SuccessNotice({ message, onDismiss }: Readonly<SuccessNoticeProps>) {
    useEffect(() => {
        const timer = setTimeout(onDismiss, DISMISS_AFTER_MS);
        return () => clearTimeout(timer);
    }, [onDismiss]);

    return (
        <div
            role="status"
            className="rounded-2xl border border-sd-success/30 bg-sd-success-bg p-4 text-sm text-sd-success"
        >
            {message}
        </div>
    );
}
