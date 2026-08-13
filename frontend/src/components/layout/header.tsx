import { Bell, ChevronDown, Search, Sun } from "lucide-react";

import { Avatar } from "@/components/ui/Avatar";
import { useAuth } from "@/hooks/auth-context";

type HeaderProps = {
  title: string;
  description?: string;
  /**
   * Unread notification count from issue #45's notifications endpoint;
   * never computed client-side. Renders the brand bell count badge
   * (16px `#912B90` circle, white bold 10); hidden while zero/unwired.
   */
  notificationCount?: number;
  /** Global search pill, rendered only when wired. */
  searchValue?: string;
  onSearchChange?: (value: string) => void;
  searchPlaceholder?: string;
};

export function Header({
  title,
  description,
  notificationCount = 0,
  searchValue,
  onSearchChange,
  searchPlaceholder = "Search companies, deals…",
}: Readonly<HeaderProps>) {
  const { user } = useAuth();

  return (
    <header className="bg-[linear-gradient(90deg,#F9FAFB_50.33%,rgba(217,226,235,0.2)_100.65%)] px-4 py-3 rounded-t-lg border border-gray-200 flex items-center justify-between">
      <div className="">
        <h1 className="text-2xl font-bold tracking-normal leading-8 text-content-primary">
          {title}
        </h1>
        {description && (
          <p className="text-base text-content-secondary leading-6">
            {description}
          </p>
        )}
      </div>

      <div className="flex items-center gap-3 shrink-0">
        {onSearchChange && (
          <div className="relative hidden sm:block">
            <Search
              size={14}
              className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-sd-faint"
              aria-hidden="true"
            />
            <input
              type="search"
              value={searchValue ?? ""}
              onChange={(event) => onSearchChange(event.target.value)}
              placeholder={searchPlaceholder}
              aria-label={searchPlaceholder}
              className="h-9 w-64 rounded-full border border-sd-border bg-sd-surface pr-4 pl-8 text-[13px] text-sd-ink placeholder:text-sd-faint"
            />
          </div>
        )}

        <button className="relative p-2 text-gray-500 hover:text-content-primary transition-colors cursor-pointer rounded-full hover:bg-black/5">
          <Bell size={24} fill="#667085" />
          {notificationCount > 0 && (
            <span className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-sd-brand px-1 text-[10px] font-bold text-white">
              {notificationCount}
            </span>
          )}
        </button>
        <button className="p-2 text-gray-500 hover:text-content-primary transition-colors cursor-pointer rounded-full hover:bg-black/5">
          <Sun size={24} />
        </button>

        <div className="flex items-center gap-2 ml-1 cursor-pointer hover:bg-black/5 rounded-full transition-colors">
          {user ? (
            <Avatar
              name={`${user.first_name} ${user.last_name}`}
              size={32}
              color="var(--color-sd-brand)"
            />
          ) : (
            <span className="h-8 w-8 rounded-full bg-sd-surface shrink-0" />
          )}
          <ChevronDown size={24} className="text-gray-500 mr-1 shrink-0" />
        </div>
      </div>
    </header>
  );
}

export default Header;
