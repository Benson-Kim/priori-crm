/**
 * Generated-contract type seam (LIB-FE-9).
 *
 * `api-schema.d.ts` is generated from the FastAPI OpenAPI schema via
 * `npm run gen:api` (openapi-typescript). Services should import response and
 * request types from here instead of hand-mirroring Pydantic schemas, which
 * was the root cause of the duplicate-response and `vendor.phone` drift bugs.
 *
 * Usage once generated:
 *
 *   import type { Schema } from "@/lib/apiTypes";
 *   type InvoiceDuplicateResponse = Schema<"InvoiceDuplicateResponse">;
 *
 * This file intentionally does not import the generated module at runtime so
 * the app still builds before the first generation; the type-only re-export
 * is resolved by `tsc` once `api-schema.d.ts` exists (committed by CI or a
 * developer running `gen:api`).
 */

// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore - generated at build time by `npm run gen:api`
import type { components } from "./api-schema";

export type Schemas = components extends { schemas: infer S } ? S : never;
export type Schema<K extends keyof Schemas> = Schemas[K];
