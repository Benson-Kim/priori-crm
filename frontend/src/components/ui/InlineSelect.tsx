/**
 * InlineSelect -- a compact custom-styled select for toolbar/filter areas.
 *
 * Renders a styled trigger button and opens a portal-based dropdown, so the
 * options are styled by us rather than by the OS. Use this instead of a native
 * <select> wherever the OS popup would break visual consistency.
 *
 * `variant` picks the skin: `default` is the reports look (gray-300 border,
 * gray-50 fill); `sales-desk` uses that module's tokens and shared control
 * height. The desk is trying the pattern first, so the two are kept separate
 * until it is worth making one of them the default everywhere.
 */

import { cn } from "@/lib/utils";
import { ChevronDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export interface InlineSelectOption {
  value: string;
  label: string;
}

interface InlineSelectProps {
  options: ReadonlyArray<InlineSelectOption>;
  value: string;
  onChange: (value: string) => void;
  "aria-label"?: string;
  className?: string;
  variant?: "default" | "sales-desk";
  /** Rendered above the trigger and used as its accessible name. */
  label?: string;
  /** Required when `label` is given, to tie the two together. */
  id?: string;
  /** Shown when nothing is selected yet. */
  placeholder?: string;
  disabled?: boolean;
  /** Per-site trigger overrides, e.g. the borderless company picker. */
  triggerClassName?: string;
}

const TRIGGER_STYLES = {
  default: [
    "px-3 py-3 rounded-lg border border-gray-300 bg-gray-50",
    "text-base font-normal leading-6 text-gray-900",
    "hover:border-priori-purple/50",
  ].join(" "),
  "sales-desk": [
    "h-control px-3 rounded-xl border border-sd-border bg-sd-card",
    "text-[13px] font-medium text-sd-ink",
    "hover:border-sd-brand/50 hover:bg-sd-surface",
  ].join(" "),
} as const;

/** Open reads the same as focused: brand border with a flush 1px ring. */
const OPEN_STYLES = {
  default: "border-priori-purple ring-1 ring-priori-purple/20",
  "sales-desk": "border-sd-brand ring-1 ring-sd-brand",
} as const;

export function InlineSelect({
  options,
  value,
  onChange,
  "aria-label": ariaLabel,
  className,
  variant = "default",
  label,
  id,
  placeholder,
  disabled = false,
  triggerClassName,
}: InlineSelectProps) {
  const [isOpen, setIsOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [coords, setCoords] = useState({ top: 0, left: 0, width: 0 });

  const selected = options.find((o) => o.value === value);
  const selectedLabel = selected?.label ?? (value || placeholder || "");
  const labelId = label && id ? `${id}-label` : undefined;

  const open = () => {
    if (!triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    setCoords({
      top: rect.bottom + 4,
      left: rect.left,
      // The panel is exactly as wide as its invoker, so opening it does not
      // change the shape of the control.
      width: rect.width,
    });
    setIsOpen(true);
  };

  const close = () => setIsOpen(false);

  const toggle = () => (isOpen ? close() : open());

  useEffect(() => {
    if (!isOpen) return;

    function handleClickOutside(e: MouseEvent) {
      if (
        triggerRef.current &&
        !triggerRef.current.contains(e.target as Node) &&
        menuRef.current &&
        !menuRef.current.contains(e.target as Node)
      ) {
        close();
      }
    }

    function handleScroll(e: Event) {
      if (menuRef.current && menuRef.current.contains(e.target as Node)) return;
      close();
    }

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        close();
        triggerRef.current?.focus();
      }
    }

    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleKeyDown);
    window.addEventListener("scroll", handleScroll, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("scroll", handleScroll, true);
      window.removeEventListener("resize", close);
    };
  }, [isOpen]);

  const isDesk = variant === "sales-desk";

  return (
    <div className={cn(isDesk ? "flex flex-col gap-1.5" : "relative inline-block", className)}>
      {label && (
        <span
          id={labelId}
          className={cn(
            isDesk
              ? "text-[10px] font-bold tracking-[1px] text-sd-muted uppercase"
              : "text-sm font-semibold text-content-priori-purple"
          )}
        >
          {label}
        </span>
      )}
      <button
        ref={triggerRef}
        id={id}
        type="button"
        disabled={disabled}
        aria-label={ariaLabel}
        aria-labelledby={!ariaLabel && labelId ? labelId : undefined}
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        onClick={toggle}
        className={cn(
          "flex w-full items-center justify-between gap-2 cursor-pointer",
          "transition-[border-color,box-shadow,background-color] duration-150",
          "focus-visible:outline-none focus-visible:border-sd-brand focus-visible:ring-1 focus-visible:ring-sd-brand",
          "disabled:cursor-not-allowed disabled:opacity-50",
          TRIGGER_STYLES[variant],
          isOpen && OPEN_STYLES[variant],
          triggerClassName
        )}
      >
        <span className={cn("truncate", !selected && "text-sd-muted")}>{selectedLabel}</span>
        <ChevronDown
          size={16}
          className={cn(
            "shrink-0 transition-transform duration-150",
            isDesk ? "text-sd-muted" : "text-gray-400",
            isOpen && "rotate-180"
          )}
        />
      </button>

      {isOpen &&
        createPortal(
          <div
            ref={menuRef}
            role="listbox"
            className={cn(
              "fixed z-50 mt-1 max-h-72 overflow-auto bg-white shadow-lg rounded-xl",
              "sd-menu",
              isDesk ? "border border-sd-border" : "border border-gray-200"
            )}
            style={{ top: coords.top, left: coords.left, width: coords.width }}
          >
            {options.map((opt) => (
              <button
                key={opt.value}
                type="button"
                role="option"
                aria-selected={opt.value === value}
                onClick={() => {
                  onChange(opt.value);
                  close();
                  triggerRef.current?.focus();
                }}
                className={cn(
                  "flex items-center w-full text-left transition-colors",
                  isDesk ? "px-3 py-2.5 text-[13px]" : "px-4 py-2.5 text-sm",
                  opt.value === value
                    ? "bg-priori-purple/10 text-priori-purple font-semibold"
                    : "text-gray-700 hover:bg-priori-purple hover:text-white"
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>,
          document.body
        )}
    </div>
  );
}
