/**
 * Router error boundary (`errorElement`).
 *
 * Without one, react-router falls back to its own development screen
 * ("Unexpected Application Error!" plus a stack trace and a note addressed
 * to the developer) — not something a user should ever see.
 *
 * This is the outer backstop: it catches router-level failures (a loader
 * throwing, a route that cannot render its element). Errors thrown while
 * rendering a page are caught closer to the page by PageErrorBoundary, which
 * keeps the sidebar and header on screen.
 */

import { isRouteErrorResponse, useRouteError } from "react-router-dom";

import { ErrorState } from "@/components/errors/ErrorState";
import { isStaleChunkError } from "@/components/errors/stale-chunk";
import NotFoundPage from "@/pages/errors/not-found";

function errorMessage(error: unknown): string | null {
  if (isRouteErrorResponse(error)) return `${error.status} ${error.statusText}`;
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  return null;
}

export default function RouteErrorBoundary() {
  const error = useRouteError();

  // Surfaced for whoever is debugging; the user sees the page below.
  console.error("[RouteErrorBoundary]", error);

  // A route that threw a 404 Response and a URL that matched nothing are the
  // same thing to the user, so they get the same page.
  if (isRouteErrorResponse(error) && error.status === 404) {
    return <NotFoundPage />;
  }

  return (
    <ErrorState
      message={errorMessage(error)}
      staleChunk={isStaleChunkError(error)}
    />
  );
}
