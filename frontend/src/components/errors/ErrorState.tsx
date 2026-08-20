/**
 * The visual half of the error pages, shared by the router's `errorElement`
 * (RouteErrorBoundary) and the per-page class boundary (PageErrorBoundary)
 * so a failure looks the same wherever it is caught.
 */

import { AlertTriangle, Home, RotateCw } from "lucide-react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/Button";

interface ErrorStateProps {
  /** Raw message, shown in development only. */
  message?: string | null;
  staleChunk?: boolean;
}

export function ErrorState({ message, staleChunk = false }: Readonly<ErrorStateProps>) {
  return (
    <div className="flex flex-col items-center justify-center gap-6 px-6 py-16 text-center">
      <span className="flex h-14 w-14 items-center justify-center rounded-full bg-red-50 text-red-500">
        <AlertTriangle size={28} />
      </span>

      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-bold text-gray-900">
          {staleChunk ? "A new version is available" : "Something went wrong"}
        </h1>
        <p className="max-w-md text-sm text-gray-500">
          {staleChunk
            ? "This page was updated while you had the app open. Reload to pick up the latest version — your data is unaffected."
            : "We hit an unexpected error loading this page. Your data is safe; try again, or head back to the dashboard."}
        </p>
      </div>

      {/*
        The raw message is useful in development and meaningless (or
        alarming) in production, where the copy above is the whole story.
      */}
      {import.meta.env.DEV && message && (
        <pre className="max-w-xl overflow-x-auto rounded-lg bg-gray-50 px-4 py-3 text-left text-xs text-gray-600">
          {message}
        </pre>
      )}

      <div className="flex flex-wrap items-center justify-center gap-3">
        <Button
          variant="primary"
          onClick={() => window.location.reload()}
        >
          <RotateCw size={18} /> Reload page
        </Button>
        {!staleChunk && (
          <Button asChild variant="outline-secondary">
            <Link to="/dashboard">
              <Home size={18} /> Back to dashboard
            </Link>
          </Button>
        )}
      </div>
    </div>
  );
}
