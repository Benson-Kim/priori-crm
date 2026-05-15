import { cn, formatDelta } from "@/lib/utils";

type BadgeVariant =
  | "active"
  | "inactive"
  | "delta-positive"
  | "delta-negative"
  // Invoice status variants
  | "draft"
  | "sent"
  | "paid"
  | "partial"
  | "overdue"
  | "canceled";

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
  // Invoice statuses
  draft: "bg-gray-100 text-gray-600 text-xs font-medium px-2.5 py-1 rounded-full",
  sent: "bg-blue-100 text-blue-700 text-xs font-medium px-2.5 py-1 rounded-full",
  paid: "bg-emerald-100 text-emerald-700 text-xs font-medium px-2.5 py-1 rounded-full",
  partial: "bg-amber-100 text-amber-700 text-xs font-medium px-2.5 py-1 rounded-full",
  overdue: "bg-red-100 text-red-700 text-xs font-medium px-2.5 py-1 rounded-full",
  canceled: "bg-gray-100 text-gray-400 text-xs font-medium px-2.5 py-1 rounded-full",
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
