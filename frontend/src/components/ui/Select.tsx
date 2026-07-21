// components/ui/Select.tsx

import { ChevronDown } from "lucide-react";
import { forwardRef, type SelectHTMLAttributes } from "react";

import { cn, focusInput, hasErrorInput } from "@/lib/utils";

interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  error?: string;
  options: ReadonlyArray<SelectOption>;
  placeholder?: string;
  wrapperClassName?: string;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  (
    { label, error, options, placeholder, className, wrapperClassName, id, ...props },
    ref
  ) => {
    const selectId = id || label?.toLowerCase().replace(/\s+/g, "-");
    const isPlaceholderSelected = placeholder && !props.value;

    return (
      <div className="flex flex-col gap-2 w-full">
        {label && (
          <label
            htmlFor={selectId}
            className="text-sm font-semibold leading-6 text-content-priori-purple"
          >
            {label}
          </label>
        )}

        <div
          className={cn(
            "flex items-center w-full px-3 py-4 gap-3 rounded-lg border bg-gray-50 transition-all",
            error ? hasErrorInput : ["border-gray-300", focusInput],
            wrapperClassName
          )}
        >
          <select
            ref={ref}
            id={selectId}
            aria-invalid={!!error}
            className={cn(
              "flex-1 bg-transparent border-none outline-none ring-0 p-0 text-base font-normal leading-6 text-gray-900 appearance-none cursor-pointer",
              isPlaceholderSelected && "text-gray-400",
              className
            )}
            {...props}
          >
            {placeholder && (
              <option value="" disabled className="text-gray-400">
                {placeholder}
              </option>
            )}

            {options.map((opt) => (
              <option key={opt.value} value={opt.value} disabled={opt.disabled} className={cn("text-gray-900", opt.disabled && "text-gray-400")}>
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