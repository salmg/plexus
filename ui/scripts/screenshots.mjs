/**
 * Capture the screenshots the wiki uses.
 *
 * Drives a headless Chrome over the DevTools protocol, which Node speaks with
 * no dependencies at all — `WebSocket` is a global from Node 22 and CDP is JSON
 * over one socket. A screenshot step that needed Playwright would pull a second
 * browser into a repository whose whole point is running on a bench machine.
 *
 * Two things it deliberately does not do.
 *
 * **It does not need a server.** It loads a built copy from `file://`, so the
 * docs can be regenerated with nothing running.
 *
 * **It does not reach into the app.** Every state is set by clicking and typing
 * through the DOM, the way a person would, so nothing in the product exists
 * only to make documentation possible — and each shot is of the real thing
 * doing the real thing. In particular there is no "arm this graph" URL: a link
 * that could start a card relay is a hazard, not a feature.
 *
 *   npm run screenshots                     # builds, then captures
 *   node scripts/screenshots.mjs --keep     # leave Chrome up to poke at
 *
 * The shots above are of the simulator, because a `file://` page has no
 * runtime. The README also shows the editor in front of a real one, so those
 * shots need `plexus up` running first, started from the repository root so
 * the capture path below resolves inside its workspace:
 *
 *   plexus up --no-open                                    # in one terminal
 *   node scripts/screenshots.mjs --live http://127.0.0.1:8900/
 */

import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(HERE, "../../docs/images");
const BUILT = resolve(HERE, "../dist-docs/index.html");
const LIVE = (() => {
  const at = process.argv.indexOf("--live");
  return at === -1 ? null : process.argv[at + 1] ?? "http://127.0.0.1:8900/";
})();
const APP = LIVE ?? process.env.PLEXUS_APP ?? `file:///${BUILT.replace(/\\/g, "/")}`;
const PORT = 9333;
const VIEWPORT = { width: 1440, height: 900 };

const CHROME_CANDIDATES = [
  process.env.CHROME,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].filter(Boolean);

function findChrome() {
  const found = CHROME_CANDIDATES.find((path) => existsSync(path));
  if (!found) {
    throw new Error(
      "No Chrome found. Set CHROME to its path.\nLooked in:\n  " +
        CHROME_CANDIDATES.join("\n  "),
    );
  }
  return found;
}

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

/* ── Page-side helpers ────────────────────────────────────────────────────
 * Injected once and then called by the shots. Written against the rendered
 * DOM, which means a change to the markup breaks the capture loudly rather
 * than producing a screenshot of the wrong thing.
 */
const HELPERS = `
  window.__shot = {
    byText(selector, ...wanted) {
      return [...document.querySelectorAll(selector)]
        .find((el) => wanted.includes(el.textContent.trim()));
    },
    async locale(code) {
      const select = document.querySelector(".topbar__locale");
      if (!select || select.value === code) return;
      select.value = code;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      await new Promise((r) => setTimeout(r, 400));
    },
    async theme(wanted) {
      if (document.documentElement.dataset.theme === wanted) return;
      const toggle = [...document.querySelectorAll(".btn--icon")]
        .find((b) => (b.getAttribute("aria-label") || "").match(/mode|modo/i));
      toggle?.click();
      await new Promise((r) => setTimeout(r, 400));
    },
    async loadYaml(name, yaml) {
      const input = document.querySelector("input[type=file]");
      const dt = new DataTransfer();
      dt.items.add(new File([yaml], name, { type: "text/yaml" }));
      input.files = dt.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      await new Promise((r) => setTimeout(r, 700));
    },
    async confirmLoad() {
      window.__shot.byText(".sheet .btn", "Load", "Cargar")?.click();
      await new Promise((r) => setTimeout(r, 900));
    },
    async addFromPalette(title) {
      const item = [...document.querySelectorAll(".palette__item")]
        .find((b) => b.querySelector(".palette__item-title")?.textContent.trim() === title);
      item?.click();
      await new Promise((r) => setTimeout(r, 500));
    },
    async pickNode(id) {
      const node = document.querySelector('[data-testid="node-' + id + '"]');
      node?.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
      node?.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      node?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await new Promise((r) => setTimeout(r, 500));
    },
    async run() {
      window.__shot.byText(".btn", "Run", "Ejecutar")?.click();
      await new Promise((r) => setTimeout(r, 2800));
    },
    async clearGraph() {
      window.__shot.byText(".btn", "New", "Nuevo")?.click();
      await new Promise((r) => setTimeout(r, 700));
    },
    focusConsole() {
      document.querySelector(".console__term textarea")?.focus();
    },
  };
`;

/** A minimal CDP client: send a method, await its reply. */
class Session {
  constructor(socket) {
    this.socket = socket;
    this.next = 1;
    this.pending = new Map();
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      const waiting = this.pending.get(message.id);
      if (!waiting) return;
      this.pending.delete(message.id);
      if (message.error) waiting.reject(new Error(message.error.message));
      else waiting.resolve(message.result);
    });
  }

  send(method, params = {}) {
    const id = this.next++;
    this.socket.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (this.pending.delete(id)) reject(new Error(`${method} timed out`));
      }, 30000);
    });
  }

  async evaluate(expression) {
    const result = await this.send("Runtime.evaluate", {
      expression: `(async () => { ${expression} })()`,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.text ?? "the page threw");
    }
    return result.result.value;
  }

  async open(url) {
    await this.send("Page.navigate", { url });

    // Wait for a rendered node rather than a fixed delay: a build that fails
    // to boot otherwise yields eight screenshots of an empty page and says
    // nothing about why.
    for (let attempt = 0; ; attempt += 1) {
      const ready = await this.evaluate(
        "return document.querySelectorAll('.pnode').length > 0;",
      ).catch(() => false);
      if (ready) break;
      if (attempt > 60) {
        throw new Error(`${url} never rendered a node. Run npm run build:docs first.`);
      }
      await sleep(250);
    }

    await this.evaluate(HELPERS);
    await sleep(1200); // the graph fits on a timer; the terminal wants a frame
  }

  /** Type a console command the way a person would: keystrokes, then Enter. */
  async command(text) {
    await this.evaluate("__shot.focusConsole();");
    await this.send("Input.insertText", { text });
    await this.send("Input.dispatchKeyEvent", {
      type: "keyDown",
      key: "Enter",
      code: "Enter",
      windowsVirtualKeyCode: 13,
      text: "\r",
    });
    await this.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter" });
    await sleep(900);
  }

  async shot(name) {
    const { data } = await this.send("Page.captureScreenshot", { format: "png" });
    writeFileSync(resolve(OUT, `${name}.png`), Buffer.from(data, "base64"));
    console.log(`  ${name}.png`);
  }
}

async function connect() {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const targets = await fetch(`http://127.0.0.1:${PORT}/json`).then((r) => r.json());
      const page = targets.find((t) => t.type === "page");
      if (page?.webSocketDebuggerUrl) {
        const socket = new WebSocket(page.webSocketDebuggerUrl);
        await new Promise((ok, fail) => {
          socket.addEventListener("open", ok, { once: true });
          socket.addEventListener("error", fail, { once: true });
        });
        return new Session(socket);
      }
    } catch {
      // Chrome is still starting.
    }
    await sleep(250);
  }
  throw new Error("Could not reach Chrome's debugging port.");
}

/* ── Rigs the shots open, as the files a person would open ───────────────── */

const PROBE_RIG = `version: 1
name: probe a recorded card
nodes:
  - id: probe-1
    type: probe
    at: [40, 150]
    config: {depth: standard}
  - id: mutation-1
    label: sequence number
    type: mutation
    at: [330, 150]
    config:
      logMutations: false
      playbook: |
        enabled: true
        log_mutations: false

        pdol_mutations: []
        afl_mutations: []
        dol_mutations: []
        injected_commands: []

        response_mutations:
          - tag: "5F34"
            mode: replace
            value: "07"
            enabled: true
            comment: "PAN sequence number"
  - id: capture-1
    type: capture
    at: [620, 150]
    config:
      path: tests/captures/mastercard-probe.jsonl
      strict: false
edges:
  - from: probe-1:card
    to: mutation-1:terminal
  - from: mutation-1:card
    to: capture-1:terminal
`;

const SOMEONE_ELSES_RIG = `version: 1
name: relay to a phone
nodes:
  - id: acr122u-target-1
    type: acr122u.target
    at: [40, 160]
    config: {device: "acr122u:001"}
  - id: trace-1
    type: trace
    at: [300, 160]
    config: {path: logs/harvest.jsonl}
  - id: nfcgate-1
    type: nfcgate
    at: [560, 160]
    config: {host: 10.0.0.9, port: 5566, session: 1}
edges:
  - from: acr122u-target-1:card
    to: trace-1:terminal
  - from: trace-1:card
    to: nfcgate-1:terminal
`;

const yamlLiteral = (text) => JSON.stringify(text);

/**
 * The editor in front of the runtime `plexus up` serves. Same clicking and
 * typing as the rest; the difference is that Run here binds a real capture and
 * drives it through atrium's mutation engine, and the console answers from
 * the runtime rather than the simulator.
 */
async function liveShots(session) {
  await session.evaluate(`await __shot.theme("dark"); await __shot.locale("en");`);

  // 9 — a blank canvas: the palette, the hint, and the runtime's address in
  //     the top bar where the simulator would say "simulated".
  await session.evaluate(`await __shot.clearGraph();`);
  await session.shot("09-new-canvas");

  // 10 — the rig that needs no hardware, run for real, then reached into.
  //      The file the README tells a reader to open, not a copy of it.
  const example = readFileSync(resolve(HERE, "../../examples/probe-a-capture.plexus.yaml"), "utf8");
  await session.evaluate(`
    await __shot.loadYaml("probe-a-capture.plexus.yaml", ${yamlLiteral(example)});
    await __shot.confirmLoad();
    await __shot.run();
  `);
  await session.command("status");
  await session.command("tx 00B2010C00");
  await session.shot("10-live-console");
}

async function main() {
  mkdirSync(OUT, { recursive: true });

  const chrome = findChrome();
  const profile = resolve(HERE, ".chrome-screenshots");
  rmSync(profile, { recursive: true, force: true });

  console.log(`chrome: ${chrome}`);
  console.log(`app:    ${APP}`);
  const child = spawn(
    chrome,
    [
      "--headless=new",
      `--remote-debugging-port=${PORT}`,
      `--user-data-dir=${profile}`,
      `--window-size=${VIEWPORT.width},${VIEWPORT.height}`,
      "--hide-scrollbars",
      "--disable-gpu",
      "--no-first-run",
      "--no-default-browser-check",
      // ES modules from file:// count as cross-origin without this, so the
      // app would never boot. Only ever pointed at our own build output.
      "--allow-file-access-from-files",
      "about:blank",
    ],
    { stdio: "ignore" },
  );

  let session;
  try {
    session = await connect();
    await session.send("Page.enable");
    await session.send("Runtime.enable");
    console.log(`capturing into ${OUT}`);

    await session.open(APP);

    if (LIVE) {
      await liveShots(session);
      console.log("done");
      return;
    }

    // 1 — the editor as it opens: the example rig, dark, English.
    await session.evaluate(`await __shot.theme("dark"); await __shot.locale("en");`);
    await session.shot("01-overview");

    // 2 — the same rig in light mode.
    await session.evaluate(`await __shot.theme("light");`);
    await session.shot("02-light-mode");

    // 3 — Spanish, so the wiki can show it rather than claim it.
    await session.evaluate(`await __shot.theme("dark"); await __shot.locale("es");`);
    await session.shot("03-espanol");

    // 4 — a node selected: the inspector, its caveats, the playbook editor.
    await session.evaluate(
      `await __shot.locale("en"); await __shot.pickNode("mutation-1");`,
    );
    await session.shot("04-inspector");

    // 5 — a graph that will not arm: an unwired requester, Run disabled.
    await session.evaluate(`await __shot.addFromPalette("SimTrace2");`);
    await session.shot("05-checks");

    // 6 — opening a rig someone sent you, before it is loaded.
    await session.evaluate(
      `await __shot.loadYaml("someone-elses-rig.plexus.yaml", ${yamlLiteral(SOMEONE_ELSES_RIG)});`,
    );
    await session.shot("06-import-review");

    // 7 — the rig that needs no hardware, loaded and ready.
    await session.evaluate(`
      await __shot.confirmLoad();
      await __shot.loadYaml("probe-a-capture.plexus.yaml", ${yamlLiteral(PROBE_RIG)});
      await __shot.confirmLoad();
    `);
    await session.shot("07-runnable-rig");

    // 8 — the same rig after a run: telemetry on the nodes, the trace in the
    //     console. Clicked, not armed by URL.
    await session.evaluate(`await __shot.run();`);
    await session.shot("08-running");

    console.log("done");
  } finally {
    if (!process.argv.includes("--keep")) {
      try {
        await session?.send("Browser.close");
      } catch {
        child.kill();
      }
      child.kill();
    }
  }
}

main().catch((error) => {
  console.error(String(error.message ?? error));
  process.exit(1);
});
