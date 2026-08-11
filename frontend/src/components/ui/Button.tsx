import { Slot } from "@radix-ui/react-slot";
import { type VariantProps } from "class-variance-authority";
import { Loader } from 'lucide-react';
import * as React from "react";

import { cn } from "@/lib/utils";
import { buttonVariants } from "./button-variants";

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
  VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  loading?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, loading, disabled, children, asChild = false, ...props }, ref) => {
    const classes = cn("cursor-pointer", buttonVariants({ variant, size, className }));

    /*
     * `asChild` renders the caller's own element (usually a router <Link>)
     * with the button styling merged in. Radix Slot merges props into a single
     * child element, so children must be passed through untouched. Wrapping
     * them in a fragment, or adding a spinner sibling, leaves Slot nothing to
     * merge into and the styling is silently dropped. `disabled` is likewise
     * omitted, since it is not valid on an anchor.
     */
    if (asChild) {
      return (
        <Slot className={classes} ref={ref} {...props}>
          {children}
        </Slot>
      );
    }

    return (
      <button
        className={classes}
        ref={ref}
        {...props}
        disabled={loading || disabled}
      >
        {loading && <Loader className="size-5 animate-spin" />}
        {children}
      </button>
    );
  },
);
Button.displayName = "Button";

export { Button };
