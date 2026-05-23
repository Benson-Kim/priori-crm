import { ChevronLeft, ChevronRight } from "lucide-react";

import { PAGINATION_DEFAULTS } from "@/lib/constants";
import { cn } from "@/lib/utils";

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
      <div className="flex items-center gap-2 text-base text-content-secondary ">
        <span>Show</span>
        <div className="bg-pink-25 p-2 rounded-lg border border-gray-200 text-base cursor-pointer">
          <select
            value={perPage}
            onChange={(e) => onPerPageChange(Number(e.target.value))}
            className="p-2"
          >
            {PAGINATION_DEFAULTS.PER_PAGE_OPTIONS.map((opt: number) => (
              <option key={opt} value={opt} className="text-priori-purple">
                {opt}
              </option>
            ))}
          </select>
        </div>
        <span>per page</span>
      </div>

      <div className="flex items-center gap-1">
        <button
          onClick={() => onPageChange(currentPage - 1)}
          disabled={currentPage <= 1}
          className="flex items-center justify-center p-3 rounded-lg text-content-secondary hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer transition-colors"
          aria-label="Previous page"
        >
          <ChevronLeft size={24} />
        </button>

        {pages.map((page) =>
          page === "..." ? (
            <span
              key={`ellipsis-${page}`}
              className="flex items-center justify-center text-content-secondary p-3 rounded-lg"
            >
              …
            </span>
          ) : (
            <button
              key={page}
              onClick={() => onPageChange(page)}
              className={cn(
                "flex items-center justify-center rounded-lg p-3 border border-gray-200 cursor-pointer transition-all",
                currentPage === page
                  ? " text-priori-purple"
                  : "border-transparent text-content-secondary hover:border-gray-200"
              )}
            >
              {page}
            </button>
          )
        )}

        <button
          onClick={() => onPageChange(currentPage + 1)}
          disabled={currentPage >= totalPages}
          className="flex items-center justify-center p-3 rounded-lg text-content-secondary hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer transition-colors"
          aria-label="Next page"
        >
          <ChevronRight size={24} />
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
