import { ChevronDown } from "lucide-react";
import { forwardRef, type SelectHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

interface SelectOption {
  value: string;
  label: string;
}

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  error?: string;
  options: ReadonlyArray<SelectOption>;
  placeholder?: string;
  wrapperClassName?: string;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  ({ label, error, options, placeholder, className, wrapperClassName, id, ...props }, ref) => {
    const selectId = id || label?.toLowerCase().replace(/\s+/g, "-");

    return (
      <div className="flex flex-col gap-2">
        {label && (
          <label
            htmlFor={selectId}
            className="text-sm font-semibold text-content-priori-purple "
          >
            {label}
          </label>
        )}
        <div
          className={cn(
            "flex items-center w-full py-4 px-3 gap-3 rounded-lg border bg-gray-50 transition-all",
            error
              ? "border-red-300 focus-within:border-red-500 focus-within:ring-red-100"
              : "border-gray-300 focus-within:border-priori-purple focus-within:ring-priori-purple/10",
            wrapperClassName
          )}
        >
          <select
            ref={ref}
            id={selectId}
            className={cn(
              "flex-1 bg-transparent border-none outline-none ring-0 p-0 text-base font-normal leading-6 text-gray-900 placeholder:text-gray-400 appearance-none cursor-pointer",
              className
            )}
            {...props}
          >
            {placeholder && (
              <option value="" disabled>
                {placeholder}
              </option>
            )}
            {options.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>

          <ChevronDown
            size={16}
            className="shrink-0 text-content-secondary pointer-events-none"
          />
        </div>
        {error && <p className="text-xs text-red-500">{error}</p>}
      </div>
    );
  }
);

Select.displayName = "Select";
