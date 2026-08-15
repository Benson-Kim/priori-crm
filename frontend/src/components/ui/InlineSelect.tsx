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
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export interface InlineSelectOption {
  value: string;
  label: string;
  /**
   * Shown but not selectable. The line-items tax picker relies on this to
   * surface a stored-but-invalid treatment without letting anyone pick it.
   */
  disabled?: boolean;
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
  /**
   * Set by wrappers that own validation (the shared Select), so the trigger
   * carries the invalid state and points at its own error text.
   */
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
  /** Fired when the panel closes, so a wrapper can mark the field touched. */
  onClose?: () => void;
  /**
   * Show a filter box at the top of the panel. Defaults to on once the list is
   * long enough to be worth scrolling (the country list is 249 entries), which
   * is the type-ahead a native <select> used to give for free.
   */
  searchable?: boolean;
}

/** Lists at or above this length get a filter box unless told otherwise. */
const SEARCHABLE_THRESHOLD = 12;

const TRIGGER_STYLES = {
  default: [
    "px-3 py-3 rounded-lg border border-gray-300 bg-gray-50",
    "text-sm font-normal leading-6 text-gray-900",
    "hover:border-priori-purple/50",
  ].join(" "),
  /*
   * Matches the shared Input's box exactly: same border colour and radius,
   * and the same value/placeholder colours. A select and a text field
   * sitting next to each other should not read as two components. The fill
   * is applied separately, since it depends on whether there is a value.
   */
  "sales-desk": [
    "h-control px-3 rounded-lg border border-gray-300",
    "text-sm font-normal leading-6 text-gray-900",
    "hover:border-priori-purple/50",
  ].join(" "),
} as const;

/** Open reads the same as focused: brand border with a flush 1px ring. */
const OPEN_STYLES = {
  default: "border-priori-purple ring-1 ring-priori-purple/20",
  "sales-desk": "border-priori-purple ring-1 ring-priori-purple",
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
  "aria-invalid": ariaInvalid,
  "aria-describedby": ariaDescribedBy,
  onClose,
  searchable,
}: InlineSelectProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [coords, setCoords] = useState({ top: 0, left: 0, width: 0 });

  const selected = options.find((o) => o.value === value);
  const selectedLabel = selected?.label ?? (value || placeholder || "");
  const labelId = label && id ? `${id}-label` : undefined;

  const showSearch = searchable ?? options.length >= SEARCHABLE_THRESHOLD;
  const trimmedQuery = query.trim().toLowerCase();
  const visibleOptions = trimmedQuery
    ? options.filter((o) => o.label.toLowerCase().includes(trimmedQuery))
    : options;

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
    // Every open starts from the full list rather than the last search.
    setQuery("");
    setIsOpen(true);
  };

  // Stable so the dismiss listeners below can depend on it without being torn
  // down and rebound on every render.
  const close = useCallback(() => {
    setIsOpen(false);
    setQuery("");
    onClose?.();
  }, [onClose]);

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
  }, [isOpen, close]);

  const isDesk = variant === "sales-desk";

  return (
    <div className={cn(isDesk ? "flex flex-col gap-1.5" : "relative inline-block", className)}>
      {label && (
        <span
          id={labelId}
          /*
           * Same treatment as the shared Label, so a select and a text field
           * sitting side by side in a form read as the same kind of control.
           */
          className={cn(
            isDesk
              ? "text-sm font-medium text-sd-ink"
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
        aria-invalid={ariaInvalid}
        aria-describedby={ariaDescribedBy}
        onClick={toggle}
        className={cn(
          "flex w-full items-center justify-between gap-2 cursor-pointer",
          "transition-[border-color,box-shadow,background-color] duration-150",
          "focus-visible:outline-none focus-visible:border-priori-purple focus-visible:ring-1 focus-visible:ring-priori-purple",
          "disabled:cursor-not-allowed disabled:opacity-50",
          TRIGGER_STYLES[variant],
          /* Empty reads as a waiting field; filled reads as answered. */
          isDesk && (selected ? "bg-white" : "bg-gray-50"),
          isOpen && OPEN_STYLES[variant],
          triggerClassName
        )}
      >
        <span className={cn("truncate", !selected && "text-gray-400")}>{selectedLabel}</span>
        <ChevronDown
          size={16}
          className={cn(
            "shrink-0 transition-transform duration-150",
            "text-gray-400",
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
            {showSearch && (
              // Sticky so the filter stays reachable while the list scrolls.
              <div className="sticky top-0 bg-white p-2 border-b border-gray-100">
                <input
                  ref={searchRef}
                  type="text"
                  value={query}
                  autoFocus
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => {
                    // Enter takes the only sensible candidate: the first match.
                    if (e.key === "Enter") {
                      e.preventDefault();
                      const first = visibleOptions.find((o) => !o.disabled);
                      if (first) {
                        onChange(first.value);
                        close();
                        triggerRef.current?.focus();
                      }
                    }
                  }}
                  placeholder="Search..."
                  aria-label="Filter options"
                  className={cn(
                    "w-full rounded-lg border border-gray-300 bg-gray-50 px-3 py-2 text-sm",
                    "text-gray-900 placeholder:text-gray-400 outline-none",
                    "focus:border-priori-purple focus:ring-1 focus:ring-priori-purple/20"
                  )}
                />
              </div>
            )}

            {visibleOptions.length === 0 && (
              <p className="px-4 py-3 text-sm text-gray-400">No matches</p>
            )}

            {visibleOptions.map((opt) => (
              <button
                key={opt.value}
                type="button"
                role="option"
                disabled={opt.disabled}
                aria-selected={opt.value === value}
                aria-disabled={opt.disabled}
                onClick={() => {
                  if (opt.disabled) return;
                  onChange(opt.value);
                  close();
                  triggerRef.current?.focus();
                }}
                className={cn(
                  "flex items-center w-full text-left transition-colors",
                  isDesk ? "px-3 py-2.5 text-[13px]" : "px-4 py-2.5 text-sm",
                  opt.disabled
                    ? "text-gray-400 cursor-not-allowed"
                    : opt.value === value
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
