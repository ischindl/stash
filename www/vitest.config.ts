import { defineConfig } from "vitest/config";

// The CLI-docs guards read www/app/docs/cli/page.tsx and cli/main.py as text with
// node:fs, so they need no DOM — the jsdom/react wiring frontend/vitest.config.ts
// carries is deliberately absent here. `app/**` is in the include set because the
// route-level Turnstile test lives next to the route it guards.
export default defineConfig({
  test: {
    environment: "node",
    include: ["test/**/*.test.ts", "app/**/*.test.ts"],
  },
});
