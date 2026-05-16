import { cn } from "@/lib/utils";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";


export interface DropdownItem {
  key: string;
  label: string;
  icon?: ReactNode;
  danger?: boolean;
  onClick: () => void;
}

interface DropdownProps {
  items: DropdownItem[];
  trigger?: ReactNode;
  className?: string;
  disabled?: boolean;
}

export function Dropdown({ items, trigger, className, disabled }: Readonly<DropdownProps>) {
  const [isOpen, setIsOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div ref={ref} className={cn("relative inline-block", className)}>
      <button
        disabled={disabled}
        onClick={() => setIsOpen(!isOpen)}
        className="inline-flex items-center gap-2 text-sm font-medium text-priori-purple transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {trigger || (
          <>
            Actions
            <ChevronDown size={14} />
          </>
        )}
      </button>

      {isOpen && (
        <div className="absolute right-0 top-full z-50 mt-1 w-44 bg-white shadow-lg border border-border animate-in fade-in slide-in-from-top-1 duration-150 rounded-b-2xl">
          {items.map((item) => (
            <button
              key={item.key}
              onClick={() => {
                item.onClick();
                setIsOpen(false);
              }}
              className={cn(
                "group flex items-center gap-2.5 w-full px-3 py-2 text-sm text-left transition-colors last:rounded-b-2xl",
                item.danger
                  ? "text-danger hover:bg-danger/10"
                  : "text-content-priori-purple hover:bg-priori-purple hover:text-white hover:font-bold transition-all ease-in-out duration-100"
              )}
            >
              {item.icon && <span className="shrink-0">{item.icon}</span>}
              <span>{item.label}</span>
              <ChevronRight size={16} className="ml-auto opacity-0 group-hover:opacity-100 transition-opacity duration-150" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
