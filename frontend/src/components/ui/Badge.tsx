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
  | "canceled"
  // Quote status variants
  | "approved"
  | "invoiced"
  | "expired";

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
    "text-success font-medium px-2 py-0.5 rounded-full",
  "delta-negative":
    "text-danger font-medium px-2 py-0.5 rounded-full",
  // Invoice statuses
  draft: "text-gray-500 font-medium px-2.5 py-1 rounded-full",
  sent: "text-status-sent font-medium px-2.5 py-1 rounded-full",
  paid: "text-status-paid font-medium px-2.5 py-1 rounded-full",
  partial: "text-amber-700 font-medium px-2.5 py-1 rounded-full",
  overdue: "text-error-600 font-medium px-2.5 py-1 rounded-full",
  canceled: "text-gray-400 font-medium px-2.5 py-1 rounded-full",
  // Quote statuses
  approved: "text-teal-700 font-medium px-2.5 py-1 rounded-full",
  invoiced: "text-status-paid font-medium px-2.5 py-1 rounded-full",
  expired: "text-orange-600 font-medium px-2.5 py-1 rounded-full",
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
