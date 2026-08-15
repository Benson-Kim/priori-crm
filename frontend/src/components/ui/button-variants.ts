import { cva } from "class-variance-authority";

export const buttonVariants = cva(
  [
    "inline-flex gap-2 items-center justify-center whitespace-nowrap rounded-lg text-[20px] font-medium",
    "transition-colors duration-150",
    /*
     * The ring was previously set to both `ring-2` and `ring-0`, so the later
     * rule won and keyboard focus drew nothing at all. A flush 1px ring, no
     * offset, matching how the app already marks an active field.
     */
    "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-priori-purple",
    "disabled:pointer-events-none disabled:opacity-50",
  ].join(" "),
  {
    variants: {
      variant: {
        default:
          "bg-priori-purple text-white hover:bg-priori-purple/90",
        primary: "bg-priori-purple text-white hover:bg-priori-purple/90",
        danger: "bg-danger text-white hover:bg-danger/90",
        success: "bg-success text-white hover:bg-success/90",
        neutral: "bg-neutral text-white hover:bg-neutral/90",
        secondary:
          "bg-secondary text-secondary-foreground hover:bg-secondary/80",
        outline:
          "border border-priori-purple text-priori-purple bg-white hover:bg-priori-purple hover:text-white",
        ghost: "hover:bg-accent hover:text-accent-foreground",
        link: "text-priori-purple underline-offset-4 hover:underline",
        "outline-success":
          "border border-success text-success bg-white hover:bg-success hover:text-white",
        "outline-danger":
          "border border-danger text-danger bg-white hover:bg-danger hover:text-white",
        "outline-neutral":
          "border border-neutral text-neutral bg-white hover:bg-neutral hover:text-white",
        "outline-secondary":
          "border border-gray-300 text-secondary-foreground bg-white hover:bg-secondary",
        /*
         * Sales Desk button set — docs/sales-desk-designs/style-reference.md
         * §3 (branch sales-desk-designs), on the `sd-*` tokens (index.css §1):
         * Poppins 600 12–13, radius 10–12. Pair with size="sd".
         */
        "sd-primary":
          "rounded-[10px] bg-sd-brand text-[13px] font-semibold text-white hover:bg-sd-brand/90",
        "sd-outline-success":
          "rounded-[10px] border border-sd-success bg-white text-[13px] font-semibold text-sd-success hover:bg-sd-success-bg",
        "sd-outline-danger":
          "rounded-[10px] border border-sd-danger bg-white text-[13px] font-semibold text-sd-danger hover:bg-sd-danger-bg",
        "sd-secondary":
          "rounded-[10px] border border-sd-border bg-white text-[13px] font-semibold text-sd-ink hover:bg-sd-surface",
        "sd-link":
          "text-[13px] font-semibold text-sd-brand underline-offset-4 hover:underline",
      },
      size: {
        default: "px-4 py-3",
        sm: "rounded-md py-1.5 px-3",
        lg: "rounded-md py-4 px-8",
        icon: "h-10 w-10",
        /** Sales Desk buttons/inputs — radius 10–12 (style-reference.md §1). */
        sd: "rounded-[10px] px-3.5 py-2",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);