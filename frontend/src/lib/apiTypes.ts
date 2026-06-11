/**
 * Generated-contract type seam.
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
 * The services consume `Schema<...>` from this seam, so `api-schema.d.ts` must
 * exist for `tsc` to pass. This is intentional: a missing or broken generation
 * now fails the type-check instead of silently passing under a `@ts-ignore`,
 * so the contract is both generated AND enforced. Run `npm run gen:api` before
 * type-checking locally (CI runs it in `ui:lint-build`).
 */

import type { components } from "./api-schema";

export type Schemas = components extends { schemas: infer S } ? S : never;
export type Schema<K extends keyof Schemas> = Schemas[K];
