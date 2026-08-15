// components/ui/Input.tsx

import {
  forwardRef,
  type InputHTMLAttributes,
  type ReactNode,
} from "react";

import { cn, focusInput, hasErrorInput } from "@/lib/utils";

interface InputProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, "prefix"> {
  error?: string;
  prefix?: ReactNode;
  suffix?: ReactNode;
  ghostText?: string;
  wrapperClassName?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  (
    { error, prefix, suffix, ghostText, className, wrapperClassName, ...props },
    ref
  ) => {
    const value = String(props.value ?? "");

    return (
      <div className="flex flex-col gap-1.5 w-full">
        <div
          className={cn(
            "flex items-center w-full px-3 overflow-hidden gap-3 rounded-lg border bg-gray-50 transition-all",
            error ? hasErrorInput : ["border-gray-300", focusInput],
            wrapperClassName
          )}
        >
          {prefix && (
            <span className="shrink-0 flex items-center text-base font-normal leading-6 text-gray-900">
              {prefix}
            </span>
          )}

          <div className="relative flex-1 min-w-0">
            {ghostText && (
              <div className="pointer-events-none absolute inset-0 flex items-center text-base font-normal leading-6">
                <span className="invisible whitespace-pre">{value}</span>
                <span className="text-gray-400 whitespace-pre">{ghostText}</span>
              </div>
            )}

            <input
              ref={ref}
              aria-invalid={!!error}
              className={cn(
                "relative z-10 ui-input w-full bg-transparent border-none outline-none ring-0 px-0 py-3 text-base font-normal leading-6 text-gray-900 placeholder:text-gray-400",
                className
              )}
              {...props}
            />
          </div>

          {suffix && (
            <span className="shrink-0 flex items-center">
              {suffix}
            </span>
          )}
        </div>

        {error && <p className="text-xs text-red-500">{error}</p>}
      </div>
    );
  }
);

Input.displayName = "Input";