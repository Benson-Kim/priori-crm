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
              "peer-focus-visible:ring-2 peer-focus-visible:ring-primary/30",
              // The tick is a descendant of this box, not a sibling of the
              // input, so `peer-checked:` cannot target it directly.
              "peer-checked:[&_svg]:opacity-100"
            )}
          >
            <Check size={12} className="text-white opacity-0 transition-opacity" />
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
