import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

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
});
