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
            "flex items-center w-full px-3 overflow-hidden gap-3 rounded-lg border transition-all",
            /*
             * Empty reads as a grey well, filled reads white — the same rule
             * the Sales Desk select follows, so a field and a select sitting
             * side by side agree about what "has a value" looks like.
             *
             * Driven off the DOM rather than the `value` prop: a dozen fields
             * are wired with react-hook-form's `register`, which passes no
             * `value`, and those would never have turned white. `Input`
             * guarantees a placeholder below so `:placeholder-shown` is a
             * reliable proxy for emptiness even on fields that set none.
             */
            "bg-gray-50 has-[input:not(:placeholder-shown)]:bg-white",
            error ? hasErrorInput : ["border-gray-300", focusInput],
            wrapperClassName
          )}
        >
          {prefix && (
            <span className="shrink-0 flex items-center text-sm font-normal leading-5 text-gray-600">
              {prefix}
            </span>
          )}

          <div className="relative flex-1 min-w-0">
            {ghostText && (
              <div className="pointer-events-none absolute inset-0 flex items-center text-sm font-normal leading-5">
                <span className="invisible whitespace-pre">{value}</span>
                <span className="text-gray-400 whitespace-pre">{ghostText}</span>
              </div>
            )}

            <input
              ref={ref}
              aria-invalid={!!error}
              className={cn(
                "relative z-10 ui-input w-full bg-transparent border-none outline-none ring-0 px-0 py-3 text-sm font-normal leading-5 text-gray-600 placeholder:text-gray-400",
                className
              )}
              {...props}
              /*
               * A single space when the caller sets none, so
               * `:placeholder-shown` above still distinguishes empty from
               * filled. Without it the selector is permanently true on such a
               * field and it renders white while empty. Visually and to a
               * screen reader this is the same as having no placeholder.
               *
               * `||`, not `??`: the phone fields pass `placeholder=""` because
               * their ghost text stands in for one, and an empty string is not
               * nullish — it would slip through and leave those fields white.
               */
              placeholder={props.placeholder || " "}
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