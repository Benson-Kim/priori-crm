import { SquarePen, X } from "lucide-react";
import { useEffect, type ReactNode } from "react";

import { cn } from "@/lib/utils";
import { Button } from "./Button";

interface DialogProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  icon?: ReactNode;
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
  icon,
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
          "relative w-full max-w-2xl mx-4 rounded-xl bg-gray-100 p-6 shadow-xl overflow-hidden max-h-[90vh] overflow-y-auto",
          "animate-in fade-in zoom-in-95 duration-200"
        )}
      >
        <div className="flex items-center justify-between border-b border-gray-300 mb-8">
          <div className="flex items-center gap-2 text-gray-800">
            {icon || <SquarePen size={24} strokeWidth={2} />}
            <h3 className="text-2xl font-bold">
              {title}
            </h3>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 transition-colors"
            aria-label="Close dialog"
          >
            <X size={32} strokeWidth={2} />
          </button>
        </div>

        <div className="flex flex-col gap-8">
          {description && (
            <p className="text-sm text-gray-800 p-6 bg-white border border-gray-200 rounded-xl">
              {description}
            </p>
          )}

          {children && <div className={onConfirm ? "mb-6" : ""}>{children}</div>}

          {onConfirm && (
            <div className="flex items-center gap-2 justify-end">
              <Button
                variant="outline-secondary"
                onClick={onClose}
                className="p-4 text-[20px] leading-7.5 border-gray-600 text-gray-600"
              >
                {cancelLabel}
              </Button>
              <Button
                variant={variant === "danger" ? "primary" : "primary"}
                onClick={onConfirm}
                loading={isLoading}
                className="p-4 leading-7.5 font-sans"
              >
                {confirmLabel}
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
