import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    watch: {
      // `npm run screenshots` drives a headless Chrome with a throwaway profile
      // under `scripts/.chrome-screenshots`. It is gitignored, but vite still
      // watches it — and Chrome holds `Default/Network/Cookies` open, so the
      // watcher dies with EBUSY and takes the whole dev server down with it.
      //
      // Measured: running the screenshot script while `npm run dev` was up
      // killed the dev server with
      // `Error: EBUSY: resource busy or locked, watch '…/Network/Cookies'`.
      ignored: ["**/scripts/.chrome-screenshots/**", "**/dist-docs/**"],
    },
  },
  test: {
    // Only this project's tests. Without the include, vitest walked into
    // `scripts/.chrome-screenshots` — the throwaway Chrome profile above — and
    // tried to run the `.spec.js` files bundled inside a browser extension in
    // it: 28 failing files from someone else's code, around 13 real passes.
    include: ["tests/**/*.test.ts"],
    // Node, not jsdom: everything tested here is the graph model, the
    // validator and the store, none of which touch a DOM. The parity test also
    // reads fixture files off disk, which needs node's `fs`.
    environment: "node",
  },
});
