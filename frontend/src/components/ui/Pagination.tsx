import { ChevronLeft, ChevronRight } from "lucide-react";

import { InlineSelect } from "@/components/ui/InlineSelect";
import { PAGINATION_DEFAULTS } from "@/lib/constants";
import { cn } from "@/lib/utils";

/**
 * Same box FilterTabs' brand-outline uses (h-control, rounded-xl, 13px).
 * Plain buttons rather than the shared Button component on purpose — Button
 * bakes in its own text-[20px]/px-4 py-3 regardless of variant, and fighting
 * that via className overrides is exactly the fragile pattern FilterTabs
 * itself avoids by not using Button either.
 */
// `w-control`, not just `h-control`: without a matching fixed width, a
// single digit shrink-wraps to ~31px against a 44px height and reads as a
// narrow, cramped pill next to the wider "Show N" select. Squaring it off
// also matches the prev/next arrow buttons, which already are w-control.
const PAGE_BUTTON_BASE =
  "flex h-control w-control shrink-0 items-center justify-center rounded-xl border px-3 text-[13px] font-medium transition-colors duration-150 cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed";

const PER_PAGE_OPTIONS = PAGINATION_DEFAULTS.PER_PAGE_OPTIONS.map(
  (opt: number) => ({ value: String(opt), label: String(opt) })
);

interface PaginationProps {
  currentPage: number;
  totalPages: number;
  perPage: number;
  onPageChange: (page: number) => void;
  onPerPageChange: (perPage: number) => void;
  className?: string;
}

export function Pagination({
  currentPage,
  totalPages,
  perPage,
  onPageChange,
  onPerPageChange,
  className,
}: Readonly<PaginationProps>) {
  const pages = buildPageNumbers(currentPage, totalPages);

  return (
    <div
      className={cn(
        "flex flex-col sm:flex-row items-center justify-between gap-4",
        className
      )}
    >
      <div className="flex items-center gap-2 text-sm text-content-secondary ">
        <span>Show</span>
        <InlineSelect
          options={PER_PAGE_OPTIONS}
          value={String(perPage)}
          onChange={(value) => onPerPageChange(Number(value))}
          aria-label="Results per page"
          // Same box FilterTabs' brand-outline uses (h-control, rounded-xl,
          // 13px), just with this control's own bg/border/text colours. No
          // forced width: with `justify-between` in the trigger, a wider box
          // just stretches the gap between the number and the chevron
          // instead of adding padding around them.
          triggerClassName="h-control rounded-xl border-gray-200 bg-pink-25 px-3 text-[13px] font-medium text-priori-purple"
        />
        <span>per page</span>
      </div>

      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => onPageChange(currentPage - 1)}
          disabled={currentPage <= 1}
          aria-label="Previous page"
          className={cn(PAGE_BUTTON_BASE, "border-transparent text-content-secondary hover:bg-surface-app hover:text-content-priori-purple")}
        >
          <ChevronLeft size={16} />
        </button>

        {pages.map((page) =>
          page === "..." ? (
            <span
              key={`ellipsis-${page}`}
              className="flex h-control w-control shrink-0 items-center justify-center text-[13px] text-content-secondary"
            >
              …
            </span>
          ) : (
            <button
              type="button"
              key={page}
              onClick={() => onPageChange(page)}
              className={cn(
                PAGE_BUTTON_BASE,
                currentPage === page
                  ? "bg-pink-25 text-priori-purple border-priori-purple font-semibold"
                  : "border-transparent text-content-secondary hover:bg-surface-app hover:text-content-priori-purple"
              )}
            >
              {page}
            </button>
          )
        )}

        <button
          type="button"
          onClick={() => onPageChange(currentPage + 1)}
          disabled={currentPage >= totalPages}
          aria-label="Next page"
          className={cn(PAGE_BUTTON_BASE, "border-transparent text-content-secondary hover:bg-surface-app hover:text-content-priori-purple")}
        >
          <ChevronRight size={16} />
        </button>
      </div>
    </div>
  );
}

function buildPageNumbers(current: number, total: number): (number | "...")[] {
  const maxVisible = 7; // Show 7 page numbers at a time

  if (total <= maxVisible) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }

  const pages: (number | "...")[] = [];

  // Always show first page
  pages.push(1);

  // Calculate range around current page
  let start = Math.max(2, current - 2);
  let end = Math.min(total - 1, current + 2);

  // Adjust range to maintain 7 visible items
  if (current <= 4) {
    end = Math.min(total - 1, 6);
  } else if (current >= total - 3) {
    start = Math.max(2, total - 5);
  }

  // Add ellipsis before range if needed
  if (start > 2) {
    pages.push("...");
  }

  // Add middle range
  for (let i = start; i <= end; i++) {
    pages.push(i);
  }

  // Add ellipsis after range if needed
  if (end < total - 1) {
    pages.push("...");
  }

  // Always show last page
  pages.push(total);

  return pages;
}
