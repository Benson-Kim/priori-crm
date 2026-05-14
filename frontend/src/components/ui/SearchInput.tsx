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
        "flex items-center p-3 gap-3 rounded-lg border border-border bg-white transition-colors",
        "focus-within:border-priori-purple focus-within:ring-1 focus-within:ring-primary/30",
        className
      )}
    >
      <input
        type="text"
        className="w-full bg-transparent leading-6 text-base text-content-priori-purple placeholder:text-content-gray-400 outline-none  "
        onChange={(e) => onSearchChange?.(e.target.value)}
        {...props}
      />
      <Search
        size={24}
        className="text-gray-500 shrink-0 font-light"
      />
    </div>
  );
}
