/**
 * CalendarPicker -- a styled date picker that opens as a portal.
 *
 * Two visual variants:
 *
 *   "toolbar" (default) -- compact trigger matching InlineSelect / ReportPeriodPicker.
 *                          Use in filter bars and toolbars.
 *
 *   "form"              -- full-width trigger matching the Input component (py-4,
 *                          w-full, border-gray-300, error state). Use wherever you
 *                          would have used <Input type="date">.
 *                          Supports `error` prop for red-border validation state.
 *                          Supports `disabled` prop.
 *
 * Props:
 *   value       -- selected date as YYYY-MM-DD string (or undefined)
 *   onChange    -- called with a YYYY-MM-DD string when a day is picked,
 *                  or "" when Clear is clicked
 *   min / max   -- YYYY-MM-DD bounds; out-of-range days are unclickable
 *   placeholder -- text shown when no date is selected
 *   variant     -- "toolbar" | "form"  (default "toolbar")
 *   error       -- error message string; shows below trigger in form variant
 *   disabled    -- disables the trigger button
 */

import {
  calendarKeyboardIntent,
  isDateOutOfBounds,
  nextKeyboardDate,
  preferredCalendarFocusDate,
  restoreTriggerFocus,
} from "@/lib/dateUtils";
import { cn, focusInput, hasErrorInput } from "@/lib/utils";
import { CalendarDays, ChevronDown, ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export interface CalendarPickerProps {
  value?: string;
  onChange: (date: string) => void;
  min?: string;
  max?: string;
  placeholder?: string;
  "aria-label"?: string;
  className?: string;
  /** "toolbar" = compact bar style (default); "form" = full-width Input-style */
  variant?: "toolbar" | "form";
  /** Validation error message (form variant only) */
  error?: string;
  disabled?: boolean;
  today?: string;
  id?: string;
}

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const DAY_NAMES = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];

function isoFromParts(y: number, m: number, d: number): string {
  return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

function daysInMonth(y: number, m: number): number {
  return new Date(y, m, 0).getDate();
}

function firstDayOfWeek(y: number, m: number): number {
  return new Date(y, m - 1, 1).getDay();
}

function formatDisplay(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return `${MONTH_NAMES[m - 1].slice(0, 3)} ${d}, ${y}`;
}

function todayIso(): string {
  const t = new Date();
  return isoFromParts(t.getFullYear(), t.getMonth() + 1, t.getDate());
}

export function CalendarPicker({
  value,
  onChange,
  min,
  max,
  placeholder = "Select date",
  "aria-label": ariaLabel,
  className,
  variant = "toolbar",
  error,
  disabled = false,
  today: propToday,
  id,
}: CalendarPickerProps) {
  const todayFallback = todayIso();
  const today = propToday ?? todayFallback;

  const initView = () => {
    if (value) {
      const [y, m] = value.split("-").map(Number);
      return { viewYear: y, viewMonth: m };
    }
    const now = new Date();
    return { viewYear: now.getFullYear(), viewMonth: now.getMonth() + 1 };
  };

  const [isOpen, setIsOpen] = useState(false);
  const [viewYear, setViewYear] = useState(initView().viewYear);
  const [viewMonth, setViewMonth] = useState(initView().viewMonth);
  const [coords, setCoords] = useState({ top: 0, left: 0 });
  const [focusDate, setFocusDate] = useState<string | undefined>();

  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const gridRef = useRef<HTMLDivElement>(null);

  const open = () => {
    if (disabled || !triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    setCoords({ top: rect.bottom + 4, left: rect.left });
    const init = initView();
    setViewYear(init.viewYear);
    setViewMonth(init.viewMonth);
    setIsOpen(true);
  };

  const close = () => setIsOpen(false);
  const toggle = () => (isOpen ? close() : open());

  useEffect(() => {
    if (!isOpen) return;
    function handleDown(e: MouseEvent) {
      if (
        triggerRef.current && !triggerRef.current.contains(e.target as Node) &&
        panelRef.current && !panelRef.current.contains(e.target as Node)
      ) close();
    }
    function handleScroll(e: Event) {
      if (panelRef.current && panelRef.current.contains(e.target as Node)) return;
      close();
    }
    document.addEventListener("mousedown", handleDown);
    window.addEventListener("scroll", handleScroll, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", handleDown);
      window.removeEventListener("scroll", handleScroll, true);
      window.removeEventListener("resize", close);
    };
  }, [isOpen]);

  const prevMonth = () => {
    if (viewMonth === 1) { setViewMonth(12); setViewYear(y => y - 1); }
    else setViewMonth(m => m - 1);
  };
  const nextMonth = () => {
    if (viewMonth === 12) { setViewMonth(1); setViewYear(y => y + 1); }
    else setViewMonth(m => m + 1);
  };

  const handleDayClick = (iso: string) => {
    if (min != null && iso < min) return;
    if (max != null && iso > max) return;
    onChange(iso);
    close();
  };

  // Build calendar cells
  const totalDays = daysInMonth(viewYear, viewMonth);
  const startDow = firstDayOfWeek(viewYear, viewMonth);
  const prevDays = daysInMonth(
    viewMonth === 1 ? viewYear - 1 : viewYear,
    viewMonth === 1 ? 12 : viewMonth - 1
  );

  type Cell = { day: number; type: "prev" | "curr" | "next"; iso: string };
  const cells: Cell[] = [];
  for (let i = startDow - 1; i >= 0; i--) {
    const d = prevDays - i;
    const mo = viewMonth === 1 ? 12 : viewMonth - 1;
    const yr = viewMonth === 1 ? viewYear - 1 : viewYear;
    cells.push({ day: d, type: "prev", iso: isoFromParts(yr, mo, d) });
  }
  for (let d = 1; d <= totalDays; d++) {
    cells.push({ day: d, type: "curr", iso: isoFromParts(viewYear, viewMonth, d) });
  }
  let nextDay = 1;
  while (cells.length % 7 !== 0) {
    const mo = viewMonth === 12 ? 1 : viewMonth + 1;
    const yr = viewMonth === 12 ? viewYear + 1 : viewYear;
    cells.push({ day: nextDay, type: "next", iso: isoFromParts(yr, mo, nextDay) });
    nextDay++;
  }

  const currCells = cells.filter((c) => c.type === "curr").map((c) => c.iso);

  useEffect(() => {
    if (isOpen) {
      setFocusDate(
        preferredCalendarFocusDate(currCells, value, today, min, max)
      );
    }
  }, [isOpen, viewYear, viewMonth, value, today, min, max]);

  useEffect(() => {
    if (isOpen && focusDate && gridRef.current) {
      const btn = gridRef.current.querySelector(
        `button[data-date="${focusDate}"]`
      ) as HTMLButtonElement | null;
      btn?.focus();
    }
  }, [isOpen, focusDate]);

  const handlePanelKeyDown = (e: React.KeyboardEvent) => {
    const intent = calendarKeyboardIntent(e.key);
    if (intent === "close" || intent === "leave") {
      if (intent === "close") e.preventDefault();
      close();
      if (intent === "close") restoreTriggerFocus(triggerRef.current);
      return;
    }

    if (intent === "select" && focusDate) {
      e.preventDefault();
      if (!isDateOutOfBounds(focusDate, min, max)) {
        handleDayClick(focusDate);
        restoreTriggerFocus(triggerRef.current);
      }
      return;
    }

    if (intent === "move" && focusDate) {
      e.preventDefault();
      const next = nextKeyboardDate(focusDate, e.key);
      if (next) {
        const [ny, nm] = next.split("-").map(Number);
        if (nm !== viewMonth || ny !== viewYear) {
          setViewYear(ny);
          setViewMonth(nm);
        }
        setFocusDate(next);
      }
    }
  };

  const displayLabel = value ? formatDisplay(value) : placeholder;

  // Trigger styles
  const toolbarTrigger = cn(
    "flex items-center justify-between gap-2 px-3 py-3 rounded-lg border border-gray-300 bg-gray-50",
    "text-base w-full font-normal leading-6 transition-all cursor-pointer whitespace-nowrap",
    "hover:border-priori-purple/50",
    value ? "text-gray-900" : "text-gray-400",
    isOpen && "border-priori-purple ring-1 ring-priori-purple/20",
    disabled && "opacity-50 cursor-not-allowed pointer-events-none"
  );

  const formTrigger = cn(
    "flex items-center w-full px-3 py-4 gap-3 rounded-lg border bg-gray-50 transition-all",
    error ? hasErrorInput : [focusInput, isOpen ? "border-priori-purple ring-1 ring-priori-purple/20" : "border-gray-300"],
    disabled && "opacity-50 cursor-not-allowed pointer-events-none"
  );

  return (
    <div className={cn(variant === "form" ? "flex flex-col gap-1.5 w-full" : "relative inline-block", className)}>
      <button
        ref={triggerRef}
        id={id}
        type="button"
        aria-label={ariaLabel}
        aria-expanded={isOpen}
        aria-invalid={variant === "form" && !!error}
        disabled={disabled}
        onClick={toggle}
        className={variant === "toolbar" ? toolbarTrigger : formTrigger}
      >
        {variant === "form" && (
          <CalendarDays size={18} className="shrink-0 text-gray-400" />
        )}
        <span className={cn("flex-1 text-left", !value && "text-gray-400")}>
          {displayLabel}
        </span>
        <ChevronDown
          size={16}
          className={cn(
            "shrink-0 text-gray-400 transition-transform duration-150",
            isOpen && "rotate-180"
          )}
        />
      </button>

      {variant === "form" && error && (
        <p className="text-xs text-red-500">{error}</p>
      )}

      {isOpen && createPortal(
        <div
          ref={panelRef}
          role="dialog"
          aria-label="Calendar picker"
          tabIndex={-1}
          onKeyDown={handlePanelKeyDown}
          data-calendar-picker-panel
          className="fixed z-50 bg-white shadow-xl border border-gray-200 rounded-2xl p-4 w-72 animate-in fade-in slide-in-from-top-1 duration-150 outline-none"
          style={{ top: coords.top, left: coords.left }}
        >
          {/* Month/Year navigation */}
          <div className="flex items-center justify-between mb-3">
            <button
              type="button"
              onClick={prevMonth}
              className="p-1.5 rounded-lg hover:bg-gray-100 text-gray-500 transition-colors"
            >
              <ChevronLeft size={16} />
            </button>
            <span className="text-sm font-semibold text-gray-800">
              {MONTH_NAMES[viewMonth - 1]} {viewYear}
            </span>
            <button
              type="button"
              onClick={nextMonth}
              className="p-1.5 rounded-lg hover:bg-gray-100 text-gray-500 transition-colors"
            >
              <ChevronRight size={16} />
            </button>
          </div>

          {/* Day-of-week headers */}
          <div className="grid grid-cols-7 mb-1">
            {DAY_NAMES.map((d) => (
              <div key={d} className="text-center text-xs font-medium text-gray-400 py-1">
                {d}
              </div>
            ))}
          </div>

          {/* Calendar grid */}
          <div ref={gridRef} className="grid grid-cols-7" role="grid">
            {cells.map((cell, i) => {
              const isSelected = value === cell.iso;
              const isToday = cell.iso === today;
              const outOfBounds = isDateOutOfBounds(cell.iso, min, max);
              const isDisabled = cell.type !== "curr" || outOfBounds;
              const isFocused = focusDate === cell.iso;

              return (
                <button
                  key={i}
                  type="button"
                  role="gridcell"
                  data-date={cell.iso}
                  disabled={isDisabled}
                  tabIndex={isFocused ? 0 : -1}
                  onClick={() => {
                    if (!isDisabled) {
                      handleDayClick(cell.iso);
                      restoreTriggerFocus(triggerRef.current);
                    }
                  }}
                  onFocus={() => {
                    if (!isDisabled) setFocusDate(cell.iso);
                  }}
                  className={cn(
                    "flex items-center justify-center h-8 w-8 mx-auto text-sm rounded-full outline-none",
                    "ring-2 transition-shadow duration-200",
                    cell.type !== "curr" && "text-gray-300",
                    cell.type === "curr" && !isSelected && !isDisabled && "text-gray-700 hover:bg-priori-purple/10 hover:text-priori-purple cursor-pointer",
                    isToday && !isSelected && "font-semibold text-priori-purple",
                    isSelected && "bg-priori-purple text-white font-semibold hover:bg-priori-purple/90",
                    isDisabled && cell.type === "curr" && "text-gray-300 cursor-not-allowed",
                    isFocused ? "ring-priori-purple ring-inset" : "ring-transparent"
                  )}
                >
                  {cell.day}
                </button>
              );
            })}
          </div>

          {/* Footer */}
          <div className="flex justify-between mt-3 pt-3 border-t border-gray-100">
            <button
              type="button"
              onClick={() => { onChange(""); close(); }}
              className="text-xs text-gray-500 hover:text-gray-700 transition-colors px-2 py-1 rounded hover:bg-gray-100"
            >
              Clear
            </button>
            <button
              type="button"
              onClick={() => {
                if (max == null || today <= max) {
                  onChange(today);
                  close();
                }
              }}
              disabled={max != null && today > max}
              className="text-xs text-priori-purple font-medium hover:text-priori-purple/80 transition-colors px-2 py-1 rounded hover:bg-priori-purple/10 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Today
            </button>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}
