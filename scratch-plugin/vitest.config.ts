import tsconfigPaths from 'vite-tsconfig-paths'
import { defineConfig } from 'vitest/config'

// Standalone test config for the scratch office-eval plugin. The plugin is
// loaded from TS source and is not a workspace package, so its tests are not
// part of the root `pnpm test` inventory; run them explicitly with:
//   pnpm vitest run --config scratch-plugin/vitest.config.ts
export default defineConfig({
  // The config lives inside scratch-plugin/, so pin the project root there;
  // otherwise vitest resolves `include` against the invocation cwd.
  root: import.meta.dirname,
  plugins: [tsconfigPaths({ projects: [new URL('../tsconfig.base.json', import.meta.url).pathname] })],
  test: {
    include: ['src/**/*.spec.ts'],
    pool: 'forks',
  },
})
