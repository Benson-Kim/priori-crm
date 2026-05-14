import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface CardProps {
  children: ReactNode;
  className?: string;
  padding?: "sm" | "md" | "lg";
}

const paddingStyles = {
  sm: "p-4",
  md: "p-5",
  lg: "p-6",
};

export function Card({ children, className, padding = "md" }: Readonly<CardProps>) {
  return (
    <div
      className={cn(
        "bg-gray-300 rounded-md shadow-sm border border-border/50",
        " ",
        paddingStyles[padding],
        className
      )}
    >
      {children}
    </div>
  );
}
