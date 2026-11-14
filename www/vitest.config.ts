import { defineConfig } from "vitest/config";

// `app/**` is in the include set because the route-level Turnstile test lives next to
// the route it guards.
export default defineConfig({
  test: {
    environment: "node",
    include: ["test/**/*.test.ts", "app/**/*.test.ts"],
  },
});
