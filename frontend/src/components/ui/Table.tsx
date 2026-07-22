import { cn } from "@/lib/utils";
import { ChevronDown, ChevronUp, ChevronsUpDown } from "lucide-react";
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
}: Readonly<TableProps<T>>) {

  const handleHeaderClick = (col: Column<T>) => {
    if (!sortable || !onSort) return;
    const key = col.sortKey ?? col.key;
    // Toggle: if already active, flip direction; otherwise default to desc
    const newDir: SortDirection =
      sortKey === key && sortDirection === "desc" ? "asc" : "desc";
    onSort(key, newDir);
  };

  return (
    <div className={cn("w-full overflow-x-auto", className)}>
      <table className="w-full min-w-150">
        <thead>
          <tr className="border-b border-border ">
            {columns.map((col) => {
              const isSortable = sortable && onSort;
              const colSortKey = col.sortKey ?? col.key;
              const isActive = isSortable && sortKey === colSortKey;
              return (
                <th
                  key={col.key}
                  onClick={isSortable ? () => handleHeaderClick(col) : undefined}
                  className={cn(
                    "p-3 text-left text-base font-bold text-content-primary",
                    "bg-gray-50 border-b border-gray-100 first:rounded-tl-lg last:rounded-tr-lg",
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
          </tr>
        </thead>
        <tbody>
          {data.length === 0 ? (
            <tr>
              <td
                colSpan={columns.length}
                className="px-4 py-12 text-center text-content-secondary "
              >
                {emptyMessage}
              </td>
            </tr>
          ) : (
            data.map((item, index) => (
              <tr
                key={rowKey(item, index)}
                onClick={() => onRowClick?.(item)}
                className={cn(
                  "border-b border-gray-100 py-4 px-3",
                  "hover:bg-surface-app/50 transition-colors",
                  onRowClick && "cursor-pointer",
                  rowClassName?.(item, index)
                )}
              >
                {columns.map((col) => (
                  <td
                    key={col.key}
                    className={cn(
                      "px-4 py-3 text-base text-content-primary leading-6",
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
              </tr>
            ))
          )}
        </tbody>
        {footer}
      </table>
    </div>
  );
}
