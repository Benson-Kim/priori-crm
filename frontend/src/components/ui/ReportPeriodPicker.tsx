/**
 * ReportPeriodPicker -- unified period selector for Reports and the
 * dashboard.
 *
 * Single trigger button showing the current selection (e.g. "Jul 2026",
 * "Q3 2026", "2026", "Last 12 months", "Jan 1 – Jul 18, 2026") that opens a
 * portal panel containing mode tabs, year navigation, and mode-specific
 * controls:
 *
 *   recent  -- rolling windows (last 7 days / last month / last 12 months)
 *   month   -- 4×3 month grid, selected month highlighted
 *   quarter -- Q1-Q4 buttons
 *   year    -- year-only, no extra content (just navigate the year)
 *   custom  -- From / To CalendarPicker fields
 *
 * `recent` exists so the screens migrating off PeriodRangePicker keep the
 * rolling presets it offered; the calendar modes can only name a fixed slice
 * of the calendar, and a rolling window has to move with today. It carries
 * no year, so the year navigation is hidden for it as it is for custom.
 *
 * Wire format: always sends range=custom + dateFrom + dateTo to the backend.
 * The 5-year max cap (1830 days) is enforced at the backend level.
 *
 * No future dates allowed in any mode (max = today).
 */

import { CalendarPicker } from "@/components/ui/CalendarPicker";
import { useReportingDate } from "@/hooks/useReportingDate";
import { calendarFromIso } from "@/lib/dateUtils";
import { clampReportPeriodForYear, currentYear, MIN_YEAR, MODE_LABELS, MONTH_SHORT, periodLabel, REPORT_PERIOD_PRESETS, type ReportPeriodFilter, type ReportPeriodMode, type ReportPeriodPreset } from "@/lib/reportUtils";
import { cn } from "@/lib/utils";
import { CalendarDays, ChevronDown, ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

interface ReportPeriodPickerProps {
  value: ReportPeriodFilter;
  onChange: (value: ReportPeriodFilter) => void;
  triggerClassName?: string;
  className?: string;
}


// Component 

export function ReportPeriodPicker({
  value,
  onChange,
  triggerClassName,
  className,
}: Readonly<ReportPeriodPickerProps>) {
  const reportingDate = useReportingDate();
  const currYear = currentYear(reportingDate);
  const minYear = MIN_YEAR(reportingDate);

  const [isOpen, setIsOpen] = useState(false);
  const [panelYear, setPanelYear] = useState(value.year);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [coords, setCoords] = useState({ top: 0, left: 0, width: 320 });

  const open = () => {
    if (!triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const gutter = 8;
    const width = Math.min(320, window.innerWidth - gutter * 2);
    let left = rect.left;

    if (left + width > window.innerWidth - gutter) {
      left = rect.right - width;
    }
    left = Math.max(
      gutter,
      Math.min(left, window.innerWidth - width - gutter)
    );
    setCoords({ top: rect.bottom + 4, left, width, });
    setPanelYear(value.year);
    setIsOpen(true);
  };

  const close = () => setIsOpen(false);
  const toggle = () => (isOpen ? close() : open());

  useEffect(() => {
    if (!isOpen) return;
    function handleDown(e: MouseEvent) {
      const target = e.target as Node;
      if (
        triggerRef.current && !triggerRef.current.contains(target) &&
        panelRef.current && !panelRef.current.contains(target) &&
        !(target instanceof Element && target.closest("[data-calendar-picker-panel]"))
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

  const handleModeChange = (mode: ReportPeriodMode) => {
    const cal = calendarFromIso(reportingDate);
    const year = panelYear;
    if (mode === "recent") {
      // Keep whatever rolling window was already chosen; otherwise open on
      // the first one so the tab is never in a half-selected state.
      onChange({ mode, year, preset: value.preset ?? REPORT_PERIOD_PRESETS[0].value });
    } else if (mode === "month") {
      onChange({ mode, year, month: cal.month });
      setPanelYear(year);
    } else if (mode === "quarter") {
      onChange({ mode, year, quarter: Math.ceil(cal.month / 3) });
    } else if (mode === "year") {
      onChange({ mode, year });
      close();
    } else {
      onChange({ mode, year, customFrom: undefined, customTo: undefined });
    }
  };

  const prevYear = () => {
    const y = Math.max(minYear, panelYear - 1);
    setPanelYear(y);
    if (value.mode !== "custom") {
      onChange(clampReportPeriodForYear(value, y, reportingDate));
    }
  };

  const nextYear = () => {
    const y = Math.min(currYear, panelYear + 1);
    setPanelYear(y);
    if (value.mode !== "custom") {
      onChange(clampReportPeriodForYear(value, y, reportingDate));
    }
  };

  const selectMonth = (month: number) => {
    onChange({ mode: "month", year: panelYear, month });
    close();
  };

  const selectQuarter = (quarter: number) => {
    onChange({ mode: "quarter", year: panelYear, quarter });
    close();
  };

  const selectPreset = (preset: ReportPeriodPreset) => {
    onChange({ mode: "recent", year: panelYear, preset });
    close();
  };

  return (
    <div className={cn("relative inline-block", className)}>
      {/* Trigger */}
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={isOpen}
        onClick={toggle}
        className={cn(
          "flex items-center gap-2 px-3 py-3 rounded-lg border border-gray-300 bg-gray-50",
          "text-base font-normal leading-6 text-gray-900 transition-all cursor-pointer whitespace-nowrap",
          "hover:border-priori-purple/50",
          isOpen && "border-priori-purple ring-1 ring-priori-purple/20",
          triggerClassName
        )}
      >
        <CalendarDays size={16} className="shrink-0 text-gray-400" />
        <span>{periodLabel(value)}</span>
        <ChevronDown
          size={16}
          className={cn(
            "shrink-0 text-gray-400 transition-transform duration-150",
            isOpen && "rotate-180"
          )}
        />
      </button>

      {/* Dropdown panel */}
      {isOpen && createPortal(
        <div
          ref={panelRef}
          className="fixed z-50 bg-white shadow-xl border border-gray-200 rounded-2xl p-4 w-80 animate-in fade-in slide-in-from-top-1 duration-150"
          style={{ top: coords.top, left: coords.left, width: coords.width, }}
        >
          {/* Mode tabs */}
          <div className="flex rounded-lg border border-gray-200 overflow-hidden mb-4">
            {(Object.keys(MODE_LABELS) as ReportPeriodMode[]).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => handleModeChange(mode)}
                className={cn(
                  "flex-1 px-1 py-2 text-xs font-medium whitespace-nowrap transition-colors",
                  value.mode === mode
                    ? "bg-priori-purple text-white"
                    : "text-gray-500 hover:bg-gray-100"
                )}
              >
                {MODE_LABELS[mode]}
              </button>
            ))}
          </div>

          {/* Year navigation (meaningless for custom and rolling windows) */}
          {value.mode !== "custom" && value.mode !== "recent" && (
            <div className="flex items-center justify-between mb-4">
              <button
                type="button"
                onClick={prevYear}
                disabled={panelYear <= minYear}
                className="p-1.5 rounded-lg hover:bg-gray-100 text-gray-500 disabled:opacity-30 transition-colors"
              >
                <ChevronLeft size={16} />
              </button>
              <span className="text-sm font-semibold text-gray-800">{panelYear}</span>
              <button
                type="button"
                onClick={nextYear}
                disabled={panelYear >= currYear}
                className="p-1.5 rounded-lg hover:bg-gray-100 text-gray-500 disabled:opacity-30 transition-colors"
              >
                <ChevronRight size={16} />
              </button>
            </div>
          )}

          {/* Rolling windows */}
          {value.mode === "recent" && (
            <div className="flex flex-col gap-1.5">
              {REPORT_PERIOD_PRESETS.map(({ value: preset, label }) => (
                <button
                  key={preset}
                  type="button"
                  onClick={() => selectPreset(preset)}
                  className={cn(
                    "px-3 py-2 rounded-lg text-sm font-medium text-left transition-colors",
                    value.preset === preset
                      ? "bg-priori-purple/10 text-priori-purple font-semibold border border-priori-purple/30"
                      : "text-gray-700 hover:bg-gray-100"
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          )}

          {/* Month grid */}
          {value.mode === "month" && (
            <div className="grid grid-cols-4 gap-1.5">
              {MONTH_SHORT.map((label, i) => {
                const m = i + 1;
                const isSelected = value.month === m && value.year === panelYear;
                const cal = calendarFromIso(reportingDate);
                const isFuture =
                  panelYear > currYear ||
                  (panelYear === currYear && m > cal.month);
                return (
                  <button
                    key={label}
                    type="button"
                    disabled={isFuture}
                    onClick={() => selectMonth(m)}
                    className={cn(
                      "py-2 rounded-lg text-sm font-medium transition-colors",
                      isSelected
                        ? "bg-priori-purple/10 text-priori-purple font-semibold border border-priori-purple/30"
                        : "text-gray-700 hover:bg-gray-100",
                      isFuture && "text-gray-300 cursor-not-allowed hover:bg-transparent"
                    )}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          )}

          {/* Quarter buttons */}
          {value.mode === "quarter" && (
            <div className="grid grid-cols-4 gap-1.5">
              {[1, 2, 3, 4].map((q) => {
                const isSelected = value.quarter === q && value.year === panelYear;
                const cal = calendarFromIso(reportingDate);
                const quarterStartMonth = (q - 1) * 3 + 1;
                const isFuture =
                  panelYear > currYear ||
                  (panelYear === currYear && quarterStartMonth > cal.month);
                return (
                  <button
                    key={q}
                    type="button"
                    disabled={isFuture}
                    onClick={() => selectQuarter(q)}
                    className={cn(
                      "py-2 rounded-lg text-sm font-medium transition-colors",
                      isSelected
                        ? "bg-priori-purple/10 text-priori-purple font-semibold border border-priori-purple/30"
                        : "text-gray-700 hover:bg-gray-100",
                      isFuture && "text-gray-300 cursor-not-allowed hover:bg-transparent"
                    )}
                  >
                    Q{q}
                  </button>
                );
              })}
            </div>
          )}

          {/* Year mode -- just the year nav, click year label to confirm */}
          {value.mode === "year" && (
            <p className="text-xs text-center text-gray-400 -mt-2">
              Use the arrows to choose a year
            </p>
          )}

          {/* Custom date fields */}
          {value.mode === "custom" && (
            <div className="flex flex-col gap-3">
              <div className="flex flex-col gap-1">
                <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">From</span>
                <CalendarPicker
                  value={value.customFrom}
                  onChange={(d) => onChange({ ...value, customFrom: d || undefined })}
                  max={value.customTo ?? reportingDate}
                  today={reportingDate}
                  placeholder="mm / dd / yyyy"
                  aria-label="Start date"
                  className="block w-full"
                />
              </div>
              <div className="flex flex-col gap-1">
                <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">To</span>
                <CalendarPicker
                  value={value.customTo}
                  onChange={(d) => {
                    onChange({ ...value, customTo: d || undefined });
                    if (value.customFrom && d) close();
                  }}
                  min={value.customFrom}
                  max={reportingDate}
                  today={reportingDate}
                  placeholder="mm / dd / yyyy"
                  aria-label="End date"
                  className="block w-full"
                />
              </div>
            </div>
          )}
        </div>,
        document.body
      )}
    </div>
  );
}
