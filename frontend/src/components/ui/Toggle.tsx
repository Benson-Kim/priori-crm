import { Check, X } from "lucide-react";
import { forwardRef } from "react";

type ToggleProps = {
     id?: string;
     name?: string;
     checked: boolean;
     onChange: (checked: boolean) => void;
     label?: string;
     disabled?: boolean;
     error?: string;
     className?: string;
     "aria-label"?: string;
};

export const Toggle = forwardRef<HTMLInputElement, ToggleProps>(
     (
          {
               id,
               name,
               checked,
               onChange,
               label,
               disabled = false,
               error,
               className = "",
               "aria-label": ariaLabel,
          },
          ref
     ) => {
          return (
               <div className={className}>
                    <label
                         className={[
                              "inline-flex items-center gap-3 select-none",
                              disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer",
                         ].join(" ")}
                    >
                         <input
                              ref={ref}
                              id={id}
                              name={name}
                              type="checkbox"
                              checked={checked}
                              disabled={disabled}
                              aria-label={ariaLabel ?? label}
                              aria-invalid={Boolean(error)}
                              onChange={(event) => onChange(event.target.checked)}
                              className="sr-only peer"
                         />

                         <span
                              className={[
                                   "relative inline-flex h-7 w-12 shrink-0 items-center rounded-full border transition-all duration-200",
                                   "peer-focus-visible:outline-none peer-focus-visible:ring-4 peer-focus-visible:ring-priori-purple/20",
                                   checked
                                        ? "border-priori-purple bg-priori-purple"
                                        : "border-gray-300 bg-gray-200",
                                   error ? "ring-2 ring-red-300" : "",
                              ].join(" ")}
                         >
                              <span
                                   className={[
                                        "absolute left-0.5 top-0.5 flex h-6 w-6 items-center justify-center rounded-full bg-white shadow-sm",
                                        "transition-all duration-200 ease-out",
                                        checked
                                             ? "translate-x-5 text-priori-purple"
                                             : "translate-x-0 text-gray-500",
                                   ].join(" ")}
                              >
                                   {checked ? (
                                        <Check size={15} strokeWidth={3} aria-hidden="true" />
                                   ) : (
                                        <X size={14} strokeWidth={3} aria-hidden="true" />
                                   )}
                              </span>
                         </span>

                         {label ? (
                              <span className="text-sm font-medium text-gray-800">{label}</span>
                         ) : null}
                    </label>

                    {error ? <p className="mt-1 text-sm text-red-600">{error}</p> : null}
               </div>
          );
     }
);

Toggle.displayName = "Toggle";