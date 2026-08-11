import { cn } from "@/lib/utils";
import { ChevronDown, ChevronRight, ChevronUp, ChevronsUpDown } from "lucide-react";
import type { ReactNode } from "react";

export type SortDirection = "asc" | "desc";

export interface Column<T> {
  key: string;
  header: string;
  render?: (item: T, index: number) => React.ReactNode;
  className?: string;
  /**
   * Override which key to use for sorting. Defaults to `key`.
   * Provide when the display key differs from the data key (e.g. formatted date).
   */
  sortKey?: string;
}

interface TableProps<T> {
  columns: Column<T>[];
  data: T[];
  rowKey: (item: T, index: number) => string;
  onRowClick?: (item: T) => void;
  rowClassName?: (item: T, index: number) => string;
  footer?: ReactNode;
  className?: string;
  emptyMessage?: string;
  /**
   * Enable column-header sort controls. When true, clicking a sortable
   * column header calls onSort. All existing callers that omit this prop
   * are unaffected — the header renders without sort icons.
   */
  sortable?: boolean;
  /** Currently active sort key (controlled). */
  sortKey?: string;
  /** Currently active sort direction (controlled). */
  sortDirection?: SortDirection;
  /**
   * Called when the user clicks a sortable column header.
   * The parent is responsible for re-sorting `data` via useTableSort.
   */
  onSort?: (key: string, direction: SortDirection) => void;
  /**
   * `default` keeps the pre-existing accounting look. `sales-desk` is the
   * shared Sales Desk table shell (style-reference.md §3): rounded-16
   * container with 1px `sd-border`, `#F6F7F9` header row with 600/13 ink
   * labels, hairline row dividers.
   */
  variant?: "default" | "sales-desk";
  /**
   * Row key of the currently selected row (sales-desk drawers): renders the
   * `#FBF0FB` background + 3px `#912B90` inner-left border highlight.
   */
  selectedKey?: string;
  /** Append a trailing muted `›` chevron cell to every row. */
  chevron?: boolean;
}

function SortIcon({
  columnKey,
  activeSortKey,
  direction,
}: {
  columnKey: string;
  activeSortKey?: string;
  direction?: SortDirection;
}) {
  if (activeSortKey !== columnKey) {
    return <ChevronsUpDown size={14} className="inline ml-1 text-gray-400" />;
  }
  return direction === "asc"
    ? <ChevronUp size={14} className="inline ml-1 text-priori-purple" />
    : <ChevronDown size={14} className="inline ml-1 text-priori-purple" />;
}

export function Table<T>({
  columns,
  data,
  rowKey,
  onRowClick,
  className,
  footer,
  rowClassName,
  emptyMessage = "No data available.",
  sortable = false,
  sortKey,
  sortDirection,
  onSort,
  variant = "default",
  selectedKey,
  chevron = false,
}: Readonly<TableProps<T>>) {
  const isSalesDesk = variant === "sales-desk";

  const handleHeaderClick = (col: Column<T>) => {
    if (!sortable || !onSort) return;
    const key = col.sortKey ?? col.key;
    // Toggle: if already active, flip direction; otherwise default to desc
    const newDir: SortDirection =
      sortKey === key && sortDirection === "desc" ? "asc" : "desc";
    onSort(key, newDir);
  };

  return (
    <div
      className={cn(
        "w-full overflow-x-auto",
        isSalesDesk && "rounded-2xl border border-sd-border bg-white",
        className
      )}
    >
      <table className="w-full min-w-150">
        <thead>
          <tr className={cn(!isSalesDesk && "border-b border-border ")}>
            {columns.map((col) => {
              const isSortable = sortable && onSort;
              const colSortKey = col.sortKey ?? col.key;
              const isActive = isSortable && sortKey === colSortKey;
              return (
                <th
                  key={col.key}
                  onClick={isSortable ? () => handleHeaderClick(col) : undefined}
                  className={cn(
                    isSalesDesk
                      ? "bg-sd-surface p-3 text-left text-[13px] font-semibold text-sd-ink"
                      : cn(
                        "p-3 text-left text-base font-bold text-content-primary",
                        "bg-gray-50 border-b border-gray-100 first:rounded-tl-lg last:rounded-tr-lg"
                      ),
                    isSortable && "cursor-pointer select-none hover:bg-gray-100",
                    isActive && "text-priori-purple",
                    col.className
                  )}
                >
                  {col.header}
                  {isSortable && (
                    <SortIcon
                      columnKey={colSortKey}
                      activeSortKey={sortKey}
                      direction={sortDirection}
                    />
                  )}
                </th>
              );
            })}
            {chevron && (
              <th
                aria-label="Open row"
                className={cn(
                  "w-8",
                  isSalesDesk
                    ? "bg-sd-surface p-3"
                    : "bg-gray-50 border-b border-gray-100 p-3 last:rounded-tr-lg"
                )}
              />
            )}
          </tr>
        </thead>
        <tbody>
          {data.length === 0 ? (
            <tr>
              <td
                colSpan={columns.length + (chevron ? 1 : 0)}
                className="px-4 py-12 text-center text-content-secondary "
              >
                {emptyMessage}
              </td>
            </tr>
          ) : (
            data.map((item, index) => {
              const key = rowKey(item, index);
              const selected = selectedKey !== undefined && key === selectedKey;
              return (
                <tr
                  key={key}
                  onClick={() => onRowClick?.(item)}
                  className={cn(
                    isSalesDesk
                      ? "border-b border-sd-border transition-colors last:border-b-0 hover:bg-sd-surface/60"
                      : cn(
                        "border-b border-gray-100 py-4 px-3",
                        "hover:bg-surface-app/50 transition-colors"
                      ),
                    // Selected row: #FBF0FB bg + 3px brand inner-left border.
                    selected &&
                    "bg-sd-brand-bg hover:bg-sd-brand-bg [&>td:first-child]:shadow-[inset_3px_0_0_0_var(--color-sd-brand)]",
                    onRowClick && "cursor-pointer",
                    rowClassName?.(item, index)
                  )}
                >
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      className={cn(
                        isSalesDesk
                          ? "px-4 py-3 text-[13px] leading-5 text-sd-ink"
                          : "px-4 py-3 text-base text-content-primary leading-6",
                        col.className
                      )}
                    >
                      {col.render
                        ? col.render(item, index)
                        : String(
                          (item as Record<string, unknown>)[col.key] ?? ""
                        )}
                    </td>
                  ))}
                  {chevron && (
                    <td className="w-8 px-3 py-3">
                      <ChevronRight
                        size={16}
                        className={cn(
                          isSalesDesk ? "text-sd-faint" : "text-gray-400"
                        )}
                        aria-hidden="true"
                      />
                    </td>
                  )}
                </tr>
              );
            })
          )}
        </tbody>
        {footer}
      </table>
    </div>
  );
}
