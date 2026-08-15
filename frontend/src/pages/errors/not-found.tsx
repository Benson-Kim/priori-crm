/**
 * 404 — no route matched.
 *
 * Rendered both by the router's catch-all (`path: "*"`) and by
 * RouteErrorBoundary when a loader throws a 404 Response, so a mistyped URL
 * and a "record does not exist" both land on the same page.
 */

import { Home, Search } from "lucide-react";
import { Link } from "react-router-dom";

import NotFoundArt from "@/assets/404-error-page.svg?react";
import { Button } from "@/components/ui/Button";

export default function NotFoundPage() {
  return (
    <div className="flex flex-col items-center justify-center gap-6 px-6 py-12 text-center">
      <NotFoundArt
        role="img"
        aria-label="Page not found"
        className="w-full max-w-sm h-auto"
      />

      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-bold text-gray-900">
          We couldn&apos;t find that page
        </h1>
        <p className="max-w-md text-sm text-gray-500">
          The link may be out of date, or the page may have been moved or
          renamed. Nothing has been lost — pick a destination below.
        </p>
      </div>

      <div className="flex flex-wrap items-center justify-center gap-3">
        <Button asChild variant="primary">
          <Link to="/dashboard">
            <Home size={18} /> Back to dashboard
          </Link>
        </Button>
        <Button asChild variant="outline-secondary">
          <Link to="/customers">
            <Search size={18} /> Browse customers
          </Link>
        </Button>
      </div>
    </div>
  );
}
