import { cn } from "@/lib/utils";

interface FilterTab {
  key: string;
  label: string;
  count?: number;
}

/**
 * `default` keeps the pre-existing accounting look. The two Sales Desk pill
 * variants differ in weight: `brand-filled` is the primary filter on a table
 * view, `brand-outline` the lighter secondary filter alongside it.
 */
type FilterTabsVariant = "default" | "brand-filled" | "brand-outline";

interface FilterTabsProps {
  tabs: FilterTab[];
  activeTab: string;
  onTabChange: (key: string) => void;
  variant?: FilterTabsVariant;
  className?: string;
}

const CONTAINER_STYLES: Record<FilterTabsVariant, string> = {
  // A tight gap so a full set of tabs stays on one line next to the search
  // box and the page's primary action, rather than pushing them off the row.
  default: "flex items-center gap-1",
  "brand-filled": "flex items-center gap-1.5",
  "brand-outline": "flex items-center gap-1.5",
};

const TAB_STYLES: Record<
  FilterTabsVariant,
  { base: string; active: string; inactive: string; count: (active: boolean) => string }
> = {
  default: {
    base: "shrink-0 whitespace-nowrap p-3 rounded-md text-base font-normal transition-all duration-200 cursor-pointer",
    active: "bg-pink-25 text-priori-purple border border-gray-200 ",
    inactive:
      "text-content-secondary hover:text-content-priori-purple hover:bg-surface-app   ",
    count: () => "ml-1 text-content-secondary",
  },
  "brand-filled": {
    base: cn(
      "inline-flex h-control shrink-0 items-center whitespace-nowrap rounded-full px-3 text-dense-md font-semibold",
      "cursor-pointer transition-colors duration-150",
      "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-sd-brand",
      "active:scale-[0.98]"
    ),
    active: "bg-sd-brand text-white hover:bg-sd-brand/90",
    inactive: "text-sd-muted hover:bg-sd-surface hover:text-sd-ink",
    count: (active) => cn("ml-1", active ? "text-white/80" : "text-sd-faint"),
  },
  "brand-outline": {
    // 12px radius, not a full pill, and the active border is brand rather
    // than the hairline grey.
    base: cn(
      "inline-flex h-control shrink-0 items-center whitespace-nowrap rounded-xl border px-3 text-dense-md font-semibold",
      "cursor-pointer transition-colors duration-150",
      "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-sd-brand",
      "active:scale-[0.98]"
    ),
    active: "bg-sd-brand-bg text-sd-brand border-sd-brand hover:bg-sd-brand-bg/70",
    inactive: "border-transparent text-sd-muted hover:bg-sd-surface hover:text-sd-ink",
    count: (active) => cn("ml-1", active ? "text-sd-brand/70" : "text-sd-faint"),
  },
};

export function FilterTabs({
  tabs,
  activeTab,
  onTabChange,
  variant = "default",
  className,
}: Readonly<FilterTabsProps>) {
  const styles = TAB_STYLES[variant];

  return (
    <div className={cn(CONTAINER_STYLES[variant], className)}>
      {tabs.map((tab) => {
        const active = activeTab === tab.key;
        return (
          <button
            key={tab.key}
            onClick={() => onTabChange(tab.key)}
            className={cn(styles.base, active ? styles.active : styles.inactive)}
          >
            {tab.label}
            {tab.count !== undefined && (
              <span className={styles.count(active)}>({tab.count})</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
