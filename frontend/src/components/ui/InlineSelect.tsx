/**
 * InlineSelect -- a compact custom-styled select for toolbar/filter areas.
 *
 * Renders a styled trigger button that looks like the ReportPeriodPicker's
 * other controls (border-gray-300, bg-gray-50, px-3 py-3). Opens a portal-based
 * dropdown menu that matches the app Dropdown component's design language.
 *
 * Use this instead of native <select> wherever the OS dropdown popup
 * would break visual consistency.
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
}

export function InlineSelect({
  options,
  value,
  onChange,
  "aria-label": ariaLabel,
  className,
}: InlineSelectProps) {
  const [isOpen, setIsOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [coords, setCoords] = useState({ top: 0, left: 0, minWidth: 0 });

  const selectedLabel = options.find((o) => o.value === value)?.label ?? value;

  const open = () => {
    if (!triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    setCoords({
      top: rect.bottom + 4,
      left: rect.left,
      minWidth: rect.width,
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

    document.addEventListener("mousedown", handleClickOutside);
    window.addEventListener("scroll", handleScroll, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      window.removeEventListener("scroll", handleScroll, true);
      window.removeEventListener("resize", close);
    };
  }, [isOpen]);

  return (
    <div className={cn("relative inline-block", className)}>
      <button
        ref={triggerRef}
        type="button"
        aria-label={ariaLabel}
        aria-expanded={isOpen}
        onClick={toggle}
        className={cn(
          "flex items-center gap-2 px-3 py-3 rounded-lg border border-gray-300 bg-gray-50",
          "text-base font-normal leading-6 text-gray-900 transition-all cursor-pointer",
          "hover:border-priori-purple/50",
          isOpen && "border-priori-purple ring-1 ring-priori-purple/20"
        )}
      >
        <span>{selectedLabel}</span>
        <ChevronDown
          size={16}
          className={cn(
            "shrink-0 text-gray-400 transition-transform duration-150",
            isOpen && "rotate-180"
          )}
        />
      </button>

      {isOpen &&
        createPortal(
          <div
            ref={menuRef}
            className="fixed z-50 mt-1 bg-white shadow-lg border border-gray-200 rounded-xl overflow-hidden animate-in fade-in slide-in-from-top-1 duration-150"
            style={{ top: coords.top, left: coords.left, minWidth: coords.minWidth }}
          >
            {options.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => {
                  onChange(opt.value);
                  close();
                }}
                className={cn(
                  "flex items-center w-full px-4 py-2.5 text-sm text-left transition-colors",
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
