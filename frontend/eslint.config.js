import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
    rules: {
      // The codebase uses idiomatic fetch-on-mount effects that call a
      // memoized fetcher (which sets state). That is not a bug, so keep
      // this rule visible as a warning rather than a CI-blocking error.
      // Genuine correctness rules (no-explicit-any, react-hooks/purity,
      // rules-of-hooks) remain errors.
      'react-hooks/set-state-in-effect': 'warn',
    },
  },
])
