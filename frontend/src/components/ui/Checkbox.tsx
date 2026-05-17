import { Check } from "lucide-react";
import { forwardRef, type InputHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

interface CheckboxProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
}

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(
  ({ label, className, id, ...props }, ref) => {
    const checkboxId = id || label?.toLowerCase().replace(/\s+/g, "-");

    return (
      <label
        htmlFor={checkboxId}
        className={cn(
          "inline-flex items-center gap-2 cursor-pointer select-none",
          className
        )}
      >
        <div className="relative">
          <input
            ref={ref}
            type="checkbox"
            id={checkboxId}
            className="peer sr-only"
            {...props}
          />
          <div
            className={cn(
              "h-4 w-4 rounded border border-border flex items-center justify-center transition-colors",
              "peer-checked:bg-priori-purple peer-checked:border-primary",
              "peer-focus-visible:ring-2 peer-focus-visible:ring-primary/30"
            )}
          >
            <Check
              size={12}
              className="text-white opacity-0 peer-checked:opacity-100 transition-opacity"
            />
          </div>
        </div>
        {label && (
          <span className="text-sm text-content-priori-purple ">
            {label}
          </span>
        )}
      </label>
    );
  }
);

Checkbox.displayName = "Checkbox";
