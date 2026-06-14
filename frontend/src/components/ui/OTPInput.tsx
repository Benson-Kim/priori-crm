import { useCallback, useEffect, useRef, useState } from "react";

import { OTP_LENGTH } from "@/lib/constants";
import { cn } from "@/lib/utils";

interface OTPInputProps {
  length?: number;
  onComplete: (code: string) => void;
  error?: string;
  disabled?: boolean;
}

export function OTPInput({
  length = OTP_LENGTH,
  onComplete,
  error,
  disabled = false,
}: Readonly<OTPInputProps>) {
  const [values, setValues] = useState<string[]>(new Array(length).fill(""));
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRefs = useRef<(HTMLInputElement | null)[]>([]);

  useEffect(() => {
    inputRefs.current[0]?.focus();
  }, []);

  const handleChange = useCallback(
    (index: number, value: string) => {
      if (!/^\d*$/.test(value)) return;

      const digit = value.slice(-1);
      const newValues = [...values];
      newValues[index] = digit;
      setValues(newValues);

      if (digit && index < length - 1) {
        inputRefs.current[index + 1]?.focus();
        setActiveIndex(index + 1);
      }

      const code = newValues.join("");
      if (code.length === length && newValues.every(Boolean)) {
        onComplete(code);
      }
    },
    [values, length, onComplete]
  );

  const handleKeyDown = useCallback(
    (index: number, e: React.KeyboardEvent) => {
      if (e.key === "Backspace" && !values[index] && index > 0) {
        inputRefs.current[index - 1]?.focus();
        setActiveIndex(index - 1);
      }
    },
    [values]
  );

  const handlePaste = useCallback(
    (e: React.ClipboardEvent) => {
      e.preventDefault();
      const pasted = e.clipboardData
        .getData("text")
        .replace(/\D/g, "")
        .slice(0, length);
      if (!pasted) return;

      const newValues = [...values];
      pasted.split("").forEach((char, i) => {
        newValues[i] = char;
      });
      setValues(newValues);

      const nextIndex = Math.min(pasted.length, length - 1);
      inputRefs.current[nextIndex]?.focus();
      setActiveIndex(nextIndex);

      if (pasted.length === length) {
        onComplete(pasted);
      }
    },
    [values, length, onComplete]
  );

  return (
    <div className="flex flex-col items-center gap-2">
      <div className="flex w-full max-w-[24rem] items-center justify-center gap-4 lg:gap-8 ">
        {values.map((val, i) => (
          <input
            key={i}
            ref={(el) => {
              inputRefs.current[i] = el;
            }}
            type="text"
            inputMode="numeric"
            maxLength={1}
            value={val}
            placeholder="−"
            disabled={disabled}
            onChange={(e) => handleChange(i, e.target.value)}
            onKeyDown={(e) => handleKeyDown(i, e)}
            onFocus={() => setActiveIndex(i)}
            onPaste={i === 0 ? handlePaste : undefined}
            className={cn(
              "flex-none h-14 w-15.25 text-center text-2xl font-bold rounded-lg border bg-white transition-all placeholder:font-bold placeholder:text-gray-800 placeholder:text-2xl",
              val && activeIndex !== i && "border-2",
              activeIndex === i
                ? "text-sky-blue focus-within:border-sky-blue focus-within:ring-sky-blue placeholder:text-sky-blue"
                : "border-gray-300 text-gray-800",
              error && "border-danger placeholder:text-danger",
              disabled && "opacity-50 cursor-not-allowed"
            )}
            aria-label={`Digit ${i + 1}`}
          />
        ))}
      </div>
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
  );
}
