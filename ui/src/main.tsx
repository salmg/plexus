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

const host = document.getElementById("root");
if (!host) throw new Error("No #root element to mount into.");

createRoot(host).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
