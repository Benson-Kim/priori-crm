import { cn } from "@/lib/utils";

interface Column<T> {
  key: string;
  header: string;
  render?: (item: T, index: number) => React.ReactNode;
  className?: string;
}

interface TableProps<T> {
  columns: Column<T>[];
  data: T[];
  rowKey: (item: T, index: number) => string;
  onRowClick?: (item: T) => void;
  className?: string;
  emptyMessage?: string;
}

export function Table<T>({
  columns,
  data,
  rowKey,
  onRowClick,
  className,
  emptyMessage = "No data available.",
}: Readonly<TableProps<T>>) {
  return (
    <div className={cn("w-full overflow-x-auto", className)}>
      <table className="w-full min-w-150">
        <thead>
          <tr className="border-b border-border ">
            {columns.map((col) => (
              <th
                key={col.key}
                className={cn(
                  "p-3 text-left text-base font-bold text-content-primary",
                  "bg-gray-50 border-b border-gray-100 first:rounded-tl-lg last:rounded-tr-lg",
                  "",
                  col.className
                )}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.length === 0 ? (
            <tr>
              <td
                colSpan={columns.length}
                className="px-4 py-12 text-center text-sm text-content-secondary "
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
                  "hover:bg-surface-app/50  transition-colors",
                  onRowClick && "cursor-pointer"
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
      </table>
    </div>
  );
}
