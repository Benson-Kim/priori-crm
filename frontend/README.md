# React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

## API contract (single source of truth)

Response/request types come from the backend OpenAPI schema, not hand-mirrored
interfaces (which previously drifted and caused the duplicate-response and
`vendor.phone` bugs.

- Generate the typed schema. The services consume these types, so
  `src/lib/api-schema.d.ts` must exist for `tsc` to pass — run this before
  type-checking locally (CI runs it in `ui:lint-build`):

  ```sh
  npm run gen:api   # writes src/lib/api-schema.d.ts
  ```

  `gen:api` resolves the schema in this order:

  1. `VITE_OPENAPI_SCHEMA` — a local `openapi.json` file. Prefer this: the API
     ships `api/scripts/export_openapi.py`, which serialises `app.openapi()`
     **offline** (no server, no DB), e.g.

     ```sh
     python api/scripts/export_openapi.py api/openapi.json
     VITE_OPENAPI_SCHEMA=../api/openapi.json npm run gen:api
     ```

  2. `VITE_OPENAPI_URL` — a running API's `/openapi.json`.
  3. `http://localhost:8000/openapi.json` — the local dev default.

- Reference generated types via the seam in `src/lib/apiTypes.ts`:

  ```ts
  import type { Schema } from "@/lib/apiTypes";
  type InvoiceDuplicateResponse = Schema<"InvoiceDuplicateResponse">;
  ```

- Which document actions are wired to a live endpoint is declared once in
  `src/lib/features.ts` (`DOCUMENT_FEATURES`) — UI controls read that map
  instead of hardcoding availability with `alert("coming soon")` stubs.

The generated `api-schema.d.ts` is not committed; CI should run `gen:api` and
type-check, or a developer regenerates it after backend contract changes.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type-aware lint rules:

```js
export default defineConfig([
  globalIgnores(["dist"]),
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      // Other configs...

      // Remove tseslint.configs.recommended and replace with this
      tseslint.configs.recommendedTypeChecked,
      // Alternatively, use this for stricter rules
      tseslint.configs.strictTypeChecked,
      // Optionally, add this for stylistic rules
      tseslint.configs.stylisticTypeChecked,

      // Other configs...
    ],
    languageOptions: {
      parserOptions: {
        project: ["./tsconfig.node.json", "./tsconfig.app.json"],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
]);
```

You can also install [eslint-plugin-react-x](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-x) and [eslint-plugin-react-dom](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-dom) for React-specific lint rules:

```js
// eslint.config.js
import reactX from "eslint-plugin-react-x";
import reactDom from "eslint-plugin-react-dom";

export default defineConfig([
  globalIgnores(["dist"]),
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      // Other configs...
      // Enable lint rules for React
      reactX.configs["recommended-typescript"],
      // Enable lint rules for React DOM
      reactDom.configs.recommended,
    ],
    languageOptions: {
      parserOptions: {
        project: ["./tsconfig.node.json", "./tsconfig.app.json"],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
]);
```
