/**
 * Per-page error boundary.
 *
 * Wrapped around every lazy page in router.tsx. Sitting inside the layout's
 * <Outlet /> rather than on the route means a page that fails to render (or
 * whose chunk cannot be fetched) leaves the sidebar and header intact, so
 * the user can navigate somewhere else instead of being dropped onto a bare
 * full-screen error.
 *
 * A class component because React only supports error boundaries as classes;
 * a `lazy()` rejection surfaces through Suspense as a render-time throw, so
 * this catches stale chunks too.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

import { ErrorState } from "@/components/errors/ErrorState";
import { isStaleChunkError } from "@/components/errors/stale-chunk";

interface PageErrorBoundaryProps {
  children: ReactNode;
}

interface PageErrorBoundaryState {
  error: Error | null;
}

export class PageErrorBoundary extends Component<
  PageErrorBoundaryProps,
  PageErrorBoundaryState
> {
  state: PageErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): PageErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[PageErrorBoundary]", error, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <ErrorState message={error.message} staleChunk={isStaleChunkError(error)} />
    );
  }
}
