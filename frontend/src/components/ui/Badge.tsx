import { cn, formatDelta } from "@/lib/utils";

type BadgeVariant = "active" | "inactive" | "delta-positive" | "delta-negative";

interface BadgeProps {
  variant: BadgeVariant;
  children?: React.ReactNode;
  value?: number;
  className?: string;
}

const variantStyles: Record<BadgeVariant, string> = {
  active: "text-success font-medium",
  inactive: "text-warning font-medium",
  "delta-positive":
    "bg-success/10 text-success text-xs font-medium px-2 py-0.5 rounded-full",
  "delta-negative":
    "bg-danger/10 text-danger text-xs font-medium px-2 py-0.5 rounded-full",
};

export function Badge({ variant, children, value, className }: Readonly<BadgeProps>) {
  const content =
    variant === "delta-positive" || variant === "delta-negative"
      ? formatDelta(value ?? 0)
      : children;

  return (
    <span className={cn(variantStyles[variant], className)}>{content}</span>
  );
}
