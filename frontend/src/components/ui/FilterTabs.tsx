import { cn } from "@/lib/utils";

interface FilterTab {
  key: string;
  label: string;
  count?: number;
}

/**
 * Visual treatment. `default` is the Business Central look; `segmented` and
 * `chip` are the Sales Desk's denser variants, a pill-in-a-track control and
 * a row of inline filter chips respectively.
 */
type FilterTabsVariant = "default" | "segmented" | "chip";

interface FilterTabsProps {
  tabs: FilterTab[];
  activeTab: string;
  onTabChange: (key: string) => void;
  className?: string;
  variant?: FilterTabsVariant;
}

const CONTAINER_CLASSES: Record<FilterTabsVariant, string> = {
  default: "flex items-center gap-6",
  segmented:
    "flex w-fit items-center gap-1 rounded-xl border border-desk-border bg-white p-1 shadow-[0_1px_1.5px_rgba(0,0,0,0.06)]",
  chip: "flex flex-wrap items-center gap-2",
};

const TAB_CLASSES: Record<FilterTabsVariant, { base: string; active: string; idle: string }> = {
  default: {
    base: "p-3 rounded-md text-base font-normal transition-all duration-200 cursor-pointer",
    active: "bg-pink-25 text-priori-purple border border-gray-200",
    idle: "text-content-secondary hover:text-content-priori-purple hover:bg-surface-app",
  },
  segmented: {
    base: "rounded-lg px-4 py-1.5 text-[13px] font-medium transition-colors cursor-pointer",
    active: "bg-priori-purple text-white",
    idle: "text-desk-muted hover:text-desk-ink",
  },
  chip: {
    base: "flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-semibold transition-colors cursor-pointer",
    active: "border-priori-purple bg-desk-accent-soft text-priori-purple",
    idle: "border-transparent text-desk-muted hover:text-desk-ink",
  },
};

const COUNT_CLASSES: Record<FilterTabsVariant, { active: string; idle: string }> = {
  default: { active: "ml-1 text-content-secondary", idle: "ml-1 text-content-secondary" },
  segmented: { active: "ml-1 text-xs opacity-70", idle: "ml-1 text-xs opacity-70" },
  chip: { active: "text-[10px] font-bold", idle: "text-[10px] font-bold text-gray-400" },
};

export function FilterTabs({
  tabs,
  activeTab,
  onTabChange,
  className,
  variant = "default",
}: Readonly<FilterTabsProps>) {
  const tabStyles = TAB_CLASSES[variant];
  const countStyles = COUNT_CLASSES[variant];

  return (
    <div className={cn(CONTAINER_CLASSES[variant], className)}>
      {tabs.map((tab) => {
        const isActive = activeTab === tab.key;
        return (
          <button
            key={tab.key}
            type="button"
            onClick={() => onTabChange(tab.key)}
            aria-pressed={isActive}
            className={cn(tabStyles.base, isActive ? tabStyles.active : tabStyles.idle)}
          >
            {tab.label}
            {tab.count !== undefined && (
              <span className={isActive ? countStyles.active : countStyles.idle}>
                {variant === "chip" ? tab.count : `(${tab.count})`}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
