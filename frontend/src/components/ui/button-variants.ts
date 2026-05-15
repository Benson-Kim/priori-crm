import { cva } from "class-variance-authority";

export const buttonVariants = cva(
  "inline-flex gap-1.5 items-center justify-center whitespace-nowrap rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-0 focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default:
          "bg-priori-purple text-priori-purple-foreground hover:bg-priori-purple/90",
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
      },
      size: {
        default: "px-4 py-4",
        sm: "rounded-md py-1.5 px-3",
        lg: "rounded-md py-4 px-8",
        icon: "h-10 w-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);

// Made with Bob
