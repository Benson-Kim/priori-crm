import { cn } from "@/lib/utils";

interface FilterTab {
  key: string;
  label: string;
  count?: number;
}

interface FilterTabsProps {
  tabs: FilterTab[];
  activeTab: string;
  onTabChange: (key: string) => void;
  className?: string;
}

export function FilterTabs({
  tabs,
  activeTab,
  onTabChange,
  className,
}: Readonly<FilterTabsProps>) {
  return (
    <div className={cn("flex items-center gap-6", className)}>
      {tabs.map((tab) => (
        <button
          key={tab.key}
          onClick={() => onTabChange(tab.key)}
          className={cn(
            "p-3 rounded-md text-base font-normal transition-all duration-200 cursor-pointer",
            activeTab === tab.key
              ? "bg-pink-25 text-priori-purple border border-gray-200 "
              : "text-content-secondary hover:text-content-priori-purple hover:bg-surface-app   "
          )}
        >
          {tab.label}
          {tab.count !== undefined && (
            <span className="ml-1 text-content-secondary">({tab.count})</span>
          )}
        </button>
      ))}
    </div>
  );
}
