import { cn } from "@/lib/utils";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

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
  const menuRef = useRef<HTMLDivElement>(null);
  const [coords, setCoords] = useState({ top: 0, right: 0 });

  const toggleDropdown = () => {
    if (!isOpen && ref.current) {
      const rect = ref.current.getBoundingClientRect();
      setCoords({
        top: rect.bottom,
        right: document.documentElement.clientWidth - rect.right,
      });
    }
    setIsOpen(!isOpen);
  };

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (
        ref.current && !ref.current.contains(e.target as Node) &&
        menuRef.current && !menuRef.current.contains(e.target as Node)
      ) {
        setIsOpen(false);
      }
    }

    function handleScroll(e: Event) {
      if (menuRef.current && menuRef.current.contains(e.target as Node)) {
        return;
      }
      setIsOpen(false);
    }

    if (isOpen) {
      document.addEventListener("mousedown", handleClickOutside);
      window.addEventListener("scroll", handleScroll, true);
      window.addEventListener("resize", handleScroll);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      window.removeEventListener("scroll", handleScroll, true);
      window.removeEventListener("resize", handleScroll);
    };
  }, [isOpen]);

  return (
    <div ref={ref} className={cn("relative inline-block", className)}>
      <button
        disabled={disabled}
        onClick={toggleDropdown}
        className="inline-flex items-center gap-2 text-sm font-medium bg-gray-50 text-priori-purple transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {trigger || (
          <>
            Actions
            <ChevronDown size={18} />
          </>
        )}
      </button>

      {isOpen && createPortal(
        <div
          ref={menuRef}
          className="fixed z-50 mt-1 min-w-44 max-w-60 bg-white shadow-lg border border-border animate-in fade-in slide-in-from-top-1 duration-150 rounded-b-2xl"
          style={{ top: coords.top, right: coords.right }}
        >
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
        </div>,
        document.body
      )}
    </div>
  );
}
