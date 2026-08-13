import { X } from "lucide-react";
import { useEffect, type ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Drawer — Sales Desk right-hand detail drawer shell.
 *
 * Verbatim to `docs/sales-desk-designs/style-reference.md` §3: right,
 * 390px, white, left shadow `-8px 0 32px rgba(0,0,0,0.10)`, header + ×
 * close, uppercase micro-label sections (`CONTACT`, `BILLING PROFILES`,
 * `STAGE RECORD`, `LOG OR MOVE`, `DEALS` — Poppins Bold 10, 1px letter
 * spacing) separated by hairline dividers.
 */

/** Section micro-label — Poppins Bold 10, letter-spacing 1px, uppercase. */
export function MicroLabel({
  children,
  className,
}: Readonly<{ children: ReactNode; className?: string }>) {
  return (
    <p
      className={cn(
        "text-[10px] font-bold tracking-[1px] text-sd-muted uppercase",
        className
      )}
    >
      {children}
    </p>
  );
}

/** Micro-labelled drawer section, divided by hairlines. */
export function DrawerSection({
  label,
  children,
  className,
}: Readonly<{ label?: string; children: ReactNode; className?: string }>) {
  return (
    <section
      className={cn(
        "flex flex-col gap-2.5 border-b border-sd-border px-5 py-4 last:border-b-0",
        className
      )}
    >
      {label && <MicroLabel>{label}</MicroLabel>}
      {children}
    </section>
  );
}

interface DrawerProps {
  isOpen: boolean;
  onClose: () => void;
  /**
   * Header block (company/deal identity, stat chips, …). Rendered next to
   * the × close button and divided from the body by a hairline.
   */
  header: ReactNode;
  /** Body — compose from `DrawerSection`s. */
  children: ReactNode;
  /** Accessible name for the dialog. */
  ariaLabel?: string;
  className?: string;
}

export function Drawer({
  isOpen,
  onClose,
  header,
  children,
  ariaLabel,
  className,
}: Readonly<DrawerProps>) {
  // Close on Escape while open (mirrors Dialog's body-scroll handling).
  useEffect(() => {
    if (!isOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-40">
      {/* Click-catcher — the design shows no dark overlay. */}
      <button
        type="button"
        aria-label="Close drawer"
        onClick={onClose}
        className="absolute inset-0 h-full w-full cursor-default bg-transparent"
        tabIndex={-1}
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel}
        className={cn(
          "absolute inset-y-0 right-0 flex w-[390px] max-w-full flex-col overflow-y-auto bg-white shadow-sd-drawer",
          className
        )}
      >
        <div className="flex items-start justify-between gap-3 border-b border-sd-border px-5 py-4">
          <div className="min-w-0 flex-1">{header}</div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 cursor-pointer rounded p-1 text-sd-muted transition-colors hover:bg-sd-surface hover:text-sd-ink"
          >
            <X size={18} />
          </button>
        </div>
        <div className="flex flex-1 flex-col">{children}</div>
      </aside>
    </div>
  );
}
