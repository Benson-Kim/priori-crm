import { Search } from "lucide-react";
import { type InputHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

interface SearchInputProps extends InputHTMLAttributes<HTMLInputElement> {
  onSearchChange?: (value: string) => void;
}

export function SearchInput({
  onSearchChange,
  className,
  ...props
}: Readonly<SearchInputProps>) {
  return (
    <div
      className={cn(
        "flex items-center px-5 py-3 gap-2 rounded-2xl border border-border bg-white transition-colors",
        "focus-within:border-priori-purple focus-within:ring-1 focus-within:ring-priori-purple/20",
        className
      )}
    >
      <input
        type="text"
        className="ui-input w-full bg-transparent leading-5 text-sm text-gray-600 placeholder:text-content-gray-400 outline-none"
        onChange={(e) => onSearchChange?.(e.target.value)}
        {...props}
      />
      <Search
        size={16}
        className="text-gray-500 shrink-0"
      />
    </div>
  );
}
