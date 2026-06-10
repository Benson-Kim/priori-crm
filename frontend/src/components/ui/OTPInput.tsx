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
      <div className="flex items-center gap-2 sm:gap-3">
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
            disabled={disabled}
            onChange={(e) => handleChange(i, e.target.value)}
            onKeyDown={(e) => handleKeyDown(i, e)}
            onFocus={() => setActiveIndex(i)}
            onPaste={i === 0 ? handlePaste : undefined}
            className={cn(
              "w-11 h-13 sm:w-12.5 sm:h-14 text-center text-xl font-bold rounded-sm border bg-white outline-none transition-all",
              " ",
              activeIndex === i
                ? "border-info ring-2 ring-info/30 text-info"
                : "border-border text-content-priori-purple ",
              error && "border-danger",
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
