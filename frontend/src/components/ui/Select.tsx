// components/ui/Select.tsx

import {
  forwardRef,
  useCallback,
  useImperativeHandle,
  useRef,
  type SelectHTMLAttributes,
} from "react";

import { InlineSelect } from "@/components/ui/InlineSelect";
import { cn, hasErrorInput } from "@/lib/utils";

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
  /** Force the panel's filter box on or off; long lists get it by default. */
  searchable?: boolean;
}

/**
 * The form select: InlineSelect's look, a real <select>'s contract.
 *
 * The options panel is drawn by InlineSelect rather than the OS, because a
 * native <select>'s popup cannot be positioned or coloured from CSS — which is
 * the whole reason this component stopped being a bare <select>. What it keeps
 * is everything forms depend on: the `error` prop and its styling, the
 * `name`/`value` pair, ref forwarding, and an onChange whose argument still has
 * a real `target`, so `e.target.value` reads the same as it always did.
 *
 * That last part is why the native <select> is still rendered (visually
 * hidden). It stays the element the ref points at and the element handed back
 * as the event target, so consumers never see a synthetic stand-in.
 *
 * Trade-off worth knowing: on touch devices this no longer opens the OS
 * picker.
 */
export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  (
    {
      label,
      error,
      options,
      placeholder,
      className,
      wrapperClassName,
      id,
      onChange,
      onBlur,
      value,
      disabled,
      name,
      searchable,
      ...props
    },
    ref
  ) => {
    const selectId = id || label?.toLowerCase().replace(/\s+/g, "-");
    const errorId = error && selectId ? `${selectId}-error` : undefined;

    const nativeRef = useRef<HTMLSelectElement>(null);
    useImperativeHandle(ref, () => nativeRef.current as HTMLSelectElement, []);

    const currentValue = value == null ? "" : String(value);

    /**
     * Drive the hidden <select> first, then report it. Setting `.value` before
     * calling onChange is what lets consumers read `e.target.value` and
     * `e.target.name` off a genuine element.
     */
    // Memoised so InlineSelect's dismiss listeners keep a stable identity.
    const handleClose = useCallback(() => {
      const element = nativeRef.current;
      if (!element) return;
      onBlur?.({
        ...({} as React.FocusEvent<HTMLSelectElement>),
        target: element,
        currentTarget: element,
      });
    }, [onBlur]);

    const handleChange = (next: string) => {
      const element = nativeRef.current;
      if (element) {
        element.value = next;
        onChange?.({
          ...({} as React.ChangeEvent<HTMLSelectElement>),
          target: element,
          currentTarget: element,
        });
      }
    };

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

        {/*
         * Kept in the DOM so the field still participates in a form submit and
         * so the forwarded ref is a real HTMLSelectElement. `tabIndex={-1}`
         * and `aria-hidden` keep it out of the keyboard and screen-reader
         * paths, which the trigger button owns instead.
         *
         * `hidden` rather than `sr-only`: sr-only is position:absolute, and
         * with no positioned ancestor it escapes the scroll container and
         * stretches the document, leaving dead scroll space below every page.
         * display:none costs nothing here — a hidden control is still
         * submitted with the form.
         */}
        <select
          ref={nativeRef}
          id={selectId}
          name={name}
          value={currentValue}
          disabled={disabled}
          onChange={() => {}}
          tabIndex={-1}
          aria-hidden="true"
          hidden
          {...props}
        >
          {placeholder && <option value="">{placeholder}</option>}
          {options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>

        <InlineSelect
          options={options}
          value={currentValue}
          onChange={handleChange}
          onClose={handleClose}
          placeholder={placeholder}
          disabled={disabled}
          searchable={searchable}
          aria-invalid={!!error}
          aria-describedby={errorId}
          className="w-full"
          triggerClassName={cn(
            // py-3 rather than the old py-4: the same box as the period picker
            // and every other InlineSelect on the page.
            error && hasErrorInput,
            wrapperClassName,
            className
          )}
        />

        {error && (
          <p id={errorId} className="text-xs text-red-500">
            {error}
          </p>
        )}
      </div>
    );
  }
);

Select.displayName = "Select";
