import { Dialog } from "@/components/ui/Dialog";
import { useState } from "react";

export interface ConfirmOptions {
    title: string;
    description: string;
    confirmLabel?: string;
    variant?: "default" | "danger";
    onConfirm: () => Promise<void>;
    children?: React.ReactNode;
}

interface ConfirmState extends ConfirmOptions {
    isOpen: boolean;
    isLoading?: boolean;
}

const CLOSED_STATE: ConfirmState = {
    isOpen: false,
    title: "",
    description: "",
    confirmLabel: "Confirm",
    variant: "default",
    isLoading: false,
    onConfirm: async () => {},
};

export function useConfirm() {
    const [state, setState] = useState<ConfirmState>(CLOSED_STATE);

    const closeConfirm = () => setState((prev) => ({ ...prev, isOpen: false }));

    const showConfirm = (options: ConfirmOptions) => {
        setState({
            ...CLOSED_STATE,
            ...options,
            isOpen: true,
            onConfirm: async () => {
                setState((prev) => ({ ...prev, isLoading: true }));
                try {
                    await options.onConfirm();
                    closeConfirm();
                } catch (err) {
                    console.error(err);
                    setState((prev) => ({ ...prev, isLoading: false }));
                    throw err; // Re-throw in case caller wants to handle it, though the dialog stays open on error
                }
            },
        });
    };

    const ConfirmDialog = (
        <Dialog
            isOpen={state.isOpen}
            onClose={closeConfirm}
            title={state.title}
            description={state.description}
            confirmLabel={state.confirmLabel}
            variant={state.variant}
            onConfirm={state.onConfirm}
            isLoading={state.isLoading}
        >
            {state.children}
        </Dialog>
    );

    return { showConfirm, ConfirmDialog };
}
