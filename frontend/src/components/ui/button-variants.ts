import { cva } from "class-variance-authority";

/**
 * The app's button set.
 *
 * Two rules keep the two halves of the platform looking like one product:
 *
 *  - `variant` owns colour only. It never sets a font size, a radius or
 *    padding, so a variant can be paired with any size and the result is
 *    predictable.
 *  - `size` owns every metric: font size, radius and padding together.
 *
 * They used to be mixed. The base carried `text-[20px]`, which each `sd-*`
 * variant overrode back down to 13px while no shared-page variant overrode it
 * at all — so the Business Central pages drew 20px buttons next to the Sales
 * Desk's 13px ones, and which radius won depended on the order the classes
 * happened to be merged in.
 */
export const buttonVariants = cva(
  [
    "inline-flex gap-2 items-center justify-center whitespace-nowrap font-medium",
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
      /* Colour only — no metrics. */
      variant: {
        /** `default` and `primary` were byte-identical; both name one style. */
        default: "bg-priori-purple text-white hover:bg-priori-purple/90",
        primary: "bg-priori-purple text-white hover:bg-priori-purple/90",
        danger: "bg-danger text-white hover:bg-danger/90",
        success: "bg-success text-white hover:bg-success/90",
        neutral: "bg-neutral text-white hover:bg-neutral/90",
        secondary:
          "bg-secondary text-secondary-foreground hover:bg-secondary/80",
        outline:
          "border border-priori-purple text-priori-purple bg-white hover:bg-priori-purple hover:text-white",
        ghost: "text-secondary-foreground hover:bg-accent hover:text-accent-foreground",
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
         * §3, on the `sd-*` tokens (index.css). Pair with size="sd", which
         * carries the desk's 13px type and 10px radius.
         */
        "sd-primary": "bg-sd-brand text-white hover:bg-sd-brand/90",
        "sd-outline-success":
          "border border-sd-success bg-white text-sd-success hover:bg-sd-success-bg",
        "sd-outline-danger":
          "border border-sd-danger bg-white text-sd-danger hover:bg-sd-danger-bg",
        "sd-secondary":
          "border border-sd-border bg-white text-sd-ink hover:bg-sd-surface",
        "sd-link": "text-sd-brand underline-offset-4 hover:underline",
      },
      /* Metrics only — font size, radius and padding travel together. */
      size: {
        default: "rounded-lg text-base px-4 py-3",
        sm: "rounded-md text-sm py-1.5 px-3",
        lg: "rounded-md text-lg py-4 px-8",
        icon: "rounded-lg h-10 w-10",
        /** Sales Desk: Poppins 600 at 13px, radius 10 (style-reference.md §1). */
        sd: "rounded-[10px] text-dense-md font-semibold px-3.5 py-2",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);
