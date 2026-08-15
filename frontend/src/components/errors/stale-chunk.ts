/**
 * Stale-chunk detection, kept out of ErrorState.tsx so that file exports
 * only components (eslint react-refresh/only-export-components).
 */

/**
 * A chunk that no longer exists on the server.
 *
 * Every page in router.tsx is `lazy()`, so a deploy that replaces the hashed
 * chunks leaves anyone still holding the old index unable to fetch them. A
 * reload genuinely fixes this one, which is why it is worth telling apart
 * from a generic failure.
 *
 * The wording differs per browser (Chrome "Failed to fetch dynamically
 * imported module", Firefox "error loading dynamically imported module",
 * Safari "Importing a module script failed"), so match all three rather than
 * one engine's phrasing.
 */
export function isStaleChunkError(error: unknown): boolean {
  const message =
    error instanceof Error
      ? error.message
      : typeof error === "string"
        ? error
        : "";
  return /dynamically imported module|Importing a module script failed/i.test(
    message
  );
}
