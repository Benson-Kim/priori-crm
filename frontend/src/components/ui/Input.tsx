// components/ui/input.tsx

import {
  forwardRef,
  type InputHTMLAttributes,
  type ReactNode,
} from "react";

import { cn } from "@/lib/utils";

interface InputProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, "prefix"> {
  error?: string;
  prefix?: ReactNode;
  suffix?: ReactNode;
  wrapperClassName?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ error, prefix, suffix, className, wrapperClassName, ...props }, ref) => {
    return (
      <div className="flex flex-col gap-1.5 w-full">
        <div
          className={cn(
            `flex items-center w-full py-4 px-3 gap-3 rounded-lg border bg-gray-50 transition-all`,
            error
              ? `border-red-300 focus-within:border-red-500 focus-within:ring-red-100`
              : `border-gray-300 focus-within:border-priori-purple focus-within:ring-priori-purple/10`,
            wrapperClassName
          )}
        >
          {prefix && (
            <span className="shrink-0 flex items-center">
              {prefix}
            </span>
          )}

          <input
            ref={ref}
            aria-invalid={!!error}
            className={cn(
              `flex-1 bg-transparent border-none outline-none ring-0 p-0 text-base font-normal leading-6 text-gray-900 placeholder:text-gray-400`,
              className
            )}
            {...props}
          />

          {suffix && (
            <span className="shrink-0 flex items-center">
              {suffix}
            </span>
          )}
        </div>

        {error && (
          <p className="text-xs text-red-500">
            {error}
          </p>
        )}
      </div>
    );
  }
);

Input.displayName = "Input";