import { X } from "lucide-react";
import { useEffect, type ReactNode } from "react";

import { cn } from "@/lib/utils";
import { Button } from "./Button";

interface DialogProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm?: () => void;
  variant?: "default" | "danger";
  isLoading?: boolean;
}

export function Dialog({
  isOpen,
  onClose,
  title,
  description,
  children,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  onConfirm,
  variant = "default",
  isLoading = false,
}: DialogProps) {
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-sm"
        onClick={onClose}
      />
      <div
        className={cn(
          "relative w-full max-w-md mx-4 rounded-lg bg-white p-6 shadow-xl",
          "",
          "animate-in fade-in zoom-in-95 duration-200"
        )}
      >
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-content-secondary hover:text-content-priori-purple   transition-colors"
          aria-label="Close dialog"
        >
          <X size={18} />
        </button>

        <h3 className="text-lg font-semibold text-content-priori-purple  mb-1">
          {title}
        </h3>
        {description && (
          <p className="text-sm text-content-secondary  mb-4">
            {description}
          </p>
        )}

        {children && <div className="mb-4">{children}</div>}

        {onConfirm && (
          <div className="flex items-center gap-3 justify-end">
            <Button variant="outlined" onClick={onClose} size="sm">
              {cancelLabel}
            </Button>
            <Button
              variant={variant === "danger" ? "danger" : "primary"}
              onClick={onConfirm}
              size="sm"
              isLoading={isLoading}
            >
              {confirmLabel}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
