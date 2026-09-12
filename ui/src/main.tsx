import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "@xyflow/react/dist/style.css";
import "@xterm/xterm/css/xterm.css";

// Ours last, so the token layer wins over the library defaults.
import "./theme/tokens.css";
import "./theme/base.css";
import "./canvas/canvas.css";
import "./panels/panels.css";

import App from "./App";
import { useLocaleStore } from "./i18n";
import { useGraph } from "./store/useGraph";
import { useRuntime } from "./store/useRuntime";

const host = document.getElementById("root");
if (!host) throw new Error("No #root element to mount into.");

// Handy from the browser console while developing — load a rig without
// clicking one together, read what the runtime last said. Not shipped.
if (import.meta.env.DEV) {
  (window as unknown as Record<string, unknown>).__plexus = {
    useGraph,
    useRuntime,
    useLocaleStore,
  };
}

createRoot(host).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
